"""
Tests for the matching logic.
"""

import pytest
from unittest.mock import MagicMock

from hubspot_sync.matching.matcher import Matcher, MatchResult, MatchType
from hubspot_sync.matching.signals import SignalCollector, MatchSignal, SignalType
from hubspot_sync.matching.scorer import Scorer, ScoredMatch
from hubspot_sync.clients.platform import Organization, User
from hubspot_sync.clients.hubspot import Company, Contact


class TestMatcher:
    """Tests for the Matcher class."""
    
    def test_match_already_linked(self, config, sample_organization, sample_company_with_platform_id):
        """Should return ALREADY_LINKED when company has matching platform ID."""
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = sample_company_with_platform_id
        
        matcher = Matcher(hubspot, config)
        result = matcher.match_organization(sample_organization)
        
        assert result.match_type == MatchType.ALREADY_LINKED
        assert result.confidence == 1.0
        assert result.matched_company == sample_company_with_platform_id
    
    def test_match_auto_link_high_confidence(self, config, sample_organization, sample_company):
        """Should auto-link when confidence exceeds threshold."""
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = None
        hubspot.search_companies_by_domain.return_value = [sample_company]
        hubspot.get_contacts_by_emails.return_value = [
            Contact(
                id="c1",
                email="admin@acme.com",
                associated_company_ids=[sample_company.id],
            )
        ]
        hubspot.get_company_by_id.return_value = sample_company
        
        matcher = Matcher(hubspot, config)
        result = matcher.match_organization(sample_organization)
        
        assert result.match_type == MatchType.AUTO_LINK
        assert result.confidence >= config.auto_link_confidence_threshold
        assert result.matched_company == sample_company
    
    def test_match_no_candidates(self, config, sample_organization):
        """Should return NO_MATCH when no candidates found."""
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = None
        hubspot.search_companies_by_domain.return_value = []
        hubspot.get_contacts_by_emails.return_value = []
        
        matcher = Matcher(hubspot, config)
        result = matcher.match_organization(sample_organization)
        
        assert result.match_type == MatchType.NO_MATCH
    
    def test_match_conflict(self, config, sample_organization):
        """Should return CONFLICT when company has different platform ID."""
        conflicting_company = Company(
            id="hs-conflict",
            name="Conflict Corp",
            domain="acme.com",
            platform_org_id="different-org-id",  # Different from sample_organization
        )
        
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = None
        hubspot.search_companies_by_domain.return_value = [conflicting_company]
        hubspot.get_contacts_by_emails.return_value = [
            Contact(
                id="c1",
                email="admin@acme.com",
                associated_company_ids=[conflicting_company.id],
            )
        ]
        hubspot.get_company_by_id.return_value = conflicting_company
        
        matcher = Matcher(hubspot, config)
        result = matcher.match_organization(sample_organization)
        
        assert result.match_type == MatchType.CONFLICT


class TestSignalCollector:
    """Tests for the SignalCollector class."""
    
    def test_collect_existing_platform_id_signal(self, config, sample_organization, sample_company_with_platform_id):
        """Should create signal for existing platform ID match."""
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = sample_company_with_platform_id
        hubspot.search_companies_by_domain.return_value = []
        hubspot.get_contacts_by_emails.return_value = []
        
        collector = SignalCollector(hubspot, config)
        signals = collector.collect_signals(sample_organization)
        
        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.EXISTING_PLATFORM_ID
        assert signals[0].confidence == 1.0
    
    def test_collect_domain_match_signal(self, config, sample_organization, sample_company):
        """Should create signal for domain match."""
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = None
        hubspot.search_companies_by_domain.return_value = [sample_company]
        hubspot.get_contacts_by_emails.return_value = []
        
        collector = SignalCollector(hubspot, config)
        signals = collector.collect_signals(sample_organization)
        
        domain_signals = [s for s in signals if s.signal_type == SignalType.DOMAIN_MATCH]
        assert len(domain_signals) > 0
        assert domain_signals[0].company == sample_company
    
    def test_skip_generic_domain(self, config):
        """Should not create domain signal for generic email domains."""
        org = Organization(
            id="org-generic",
            name="Generic Org",
            admin_user_id="user-1",
            users=[
                User(id="user-1", email="user@gmail.com", organization_id="org-generic"),
            ],
        )
        
        hubspot = MagicMock()
        hubspot.platform_org_id_property = "platform_org_id"
        hubspot.get_company_by_platform_org_id.return_value = None
        hubspot.search_companies_by_domain.return_value = []
        hubspot.get_contacts_by_emails.return_value = []
        
        collector = SignalCollector(hubspot, config)
        signals = collector.collect_signals(org)
        
        domain_signals = [s for s in signals if s.signal_type == SignalType.DOMAIN_MATCH]
        assert len(domain_signals) == 0


class TestScorer:
    """Tests for the Scorer class."""
    
    def test_score_ground_truth(self, sample_company_with_platform_id):
        """Ground truth signal should score 1.0."""
        signals = [
            MatchSignal(
                signal_type=SignalType.EXISTING_PLATFORM_ID,
                company=sample_company_with_platform_id,
                confidence=1.0,
                source="test",
            )
        ]
        
        scorer = Scorer()
        scored = scorer.score_signals(signals, "org-123")
        
        assert len(scored) == 1
        assert scored[0].score == 1.0
        assert scored[0].is_ground_truth is True
    
    def test_score_multiple_signals_boost(self, sample_company):
        """Multiple corroborating signals should boost score."""
        signals = [
            MatchSignal(
                signal_type=SignalType.DOMAIN_MATCH,
                company=sample_company,
                confidence=0.7,
                source="domain",
            ),
            MatchSignal(
                signal_type=SignalType.CONTACT_ASSOCIATION,
                company=sample_company,
                confidence=0.6,
                source="contact",
            ),
        ]
        
        scorer = Scorer()
        scored = scorer.score_signals(signals, "org-123")
        
        assert len(scored) == 1
        # Score should be higher than individual signals due to boost
        assert scored[0].score > 0.65
    
    def test_score_conflict_penalty(self, sample_company):
        """Conflicts should heavily penalize score."""
        sample_company.platform_org_id = "different-org"
        signals = [
            MatchSignal(
                signal_type=SignalType.DOMAIN_MATCH,
                company=sample_company,
                confidence=0.8,
                source="domain",
            ),
        ]
        
        scorer = Scorer()
        scored = scorer.score_signals(signals, "org-123")
        
        assert len(scored) == 1
        assert scored[0].has_conflict is True
        assert scored[0].score < 0.4  # Heavily penalized
