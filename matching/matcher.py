"""
Main matching logic orchestrator.

Coordinates signal collection, scoring, and determines match outcomes.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from clients.hubspot import HubSpotClient, Company
from clients.platform import Organization
from analytics.billing_status import BillingStatusComputer
from config import Config
from matching.signals import SignalCollector, MatchSignal
from matching.scorer import Scorer, ScoredMatch


class MatchType(str, Enum):
    """Result type for a match attempt."""
    ALREADY_LINKED = "already_linked"  # Company already has correct platform ID
    AUTO_LINK = "auto_link"  # High confidence, can auto-link
    NEEDS_REVIEW = "needs_review"  # Medium confidence, create task for review
    CONFLICT = "conflict"  # Company has different platform ID
    MULTIPLE_MATCHES = "multiple_matches"  # Multiple possible companies
    NO_MATCH = "no_match"  # No candidates found


@dataclass
class MatchResult:
    """Result of attempting to match an organization to a company."""
    match_type: MatchType
    organization: Organization
    matched_company: Optional[Company] = None
    candidates: list[ScoredMatch] = None
    confidence: float = 0.0
    message: str = ""
    
    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


class Matcher:
    """
    Main matcher class.
    
    Orchestrates the matching process from signal collection through
    to match decision.
    """
    
    def __init__(
        self,
        hubspot: HubSpotClient,
        config: Config,
        billing_computer: Optional[BillingStatusComputer] = None,
    ):
        """
        Initialize the matcher.
        
        Args:
            hubspot: HubSpot API client
            config: Configuration
            billing_computer: Optional Paddle Billing API client
        """
        self.hubspot = hubspot
        self.config = config
        self.signal_collector = SignalCollector(hubspot, config, billing_computer)
        self.scorer = Scorer()
    
    def match_organization(self, org: Organization) -> MatchResult:
        """
        Attempt to match an organization to a HubSpot company.
        
        Args:
            org: Platform organization to match
            
        Returns:
            MatchResult with outcome and details
        """
        # Collect all signals
        signals = self.signal_collector.collect_signals(org)
        
        if not signals:
            return MatchResult(
                match_type=MatchType.NO_MATCH,
                organization=org,
                message=f"No matching signals found for {org.name}",
            )
        
        # Score and aggregate signals
        scored_matches = self.scorer.score_signals(signals, org.id)
        
        if not scored_matches:
            return MatchResult(
                match_type=MatchType.NO_MATCH,
                organization=org,
                message=f"No company candidates for {org.name}",
            )
        
        # Analyze results and determine outcome
        return self._determine_outcome(org, scored_matches)
    
    def _determine_outcome(
        self, 
        org: Organization, 
        scored_matches: list[ScoredMatch],
    ) -> MatchResult:
        """Determine the match outcome from scored matches."""
        top_match = scored_matches[0]
        
        # Case 1: Already linked (ground truth)
        if top_match.is_ground_truth:
            return MatchResult(
                match_type=MatchType.ALREADY_LINKED,
                organization=org,
                matched_company=top_match.company,
                candidates=scored_matches,
                confidence=1.0,
                message=f"Organization {org.name} already linked to {top_match.company.name or top_match.company.id}",
            )
        
        # Case 2: Conflict - company has different platform ID
        if top_match.has_conflict:
            return MatchResult(
                match_type=MatchType.CONFLICT,
                organization=org,
                matched_company=top_match.company,
                candidates=scored_matches,
                confidence=top_match.score,
                message=(
                    f"Conflict: {top_match.company.name or top_match.company.id} already linked to platform org "
                    f"{top_match.company.platform_org_id}, but {org.name} ({org.id}) claims it"
                ),
            )
        
        # Case 3: Multiple strong candidates
        strong_candidates = [m for m in scored_matches if m.score >= 0.6 and not m.has_conflict]
        if len(strong_candidates) > 1:
            # Check if scores are too close (within 0.15)
            if strong_candidates[1].score >= strong_candidates[0].score - 0.15:
                return MatchResult(
                    match_type=MatchType.MULTIPLE_MATCHES,
                    organization=org,
                    candidates=scored_matches,
                    confidence=top_match.score,
                    message=(
                        f"Multiple possible companies for {org.name}: "
                        f"{', '.join(m.company.name or m.company.id for m in strong_candidates[:3])}"
                    ),
                )
        
        # Case 4: High confidence - auto link
        if top_match.score >= self.config.auto_link_confidence_threshold:
            company_name = top_match.company.name or f"Company #{top_match.company.id}"
            return MatchResult(
                match_type=MatchType.AUTO_LINK,
                organization=org,
                matched_company=top_match.company,
                candidates=scored_matches,
                confidence=top_match.score,
                message=f"Auto-linking {org.name} to {company_name} (confidence: {top_match.score:.2f})",
            )
        
        # Case 5: Medium confidence - needs review
        if top_match.score >= 0.4:
            return MatchResult(
                match_type=MatchType.NEEDS_REVIEW,
                organization=org,
                matched_company=top_match.company,
                candidates=scored_matches,
                confidence=top_match.score,
                message=(
                    f"Needs review: {org.name} might match {top_match.company.name or top_match.company.id} "
                    f"(confidence: {top_match.score:.2f})"
                ),
            )
        
        # Case 6: Low confidence - no clear match
        return MatchResult(
            match_type=MatchType.NO_MATCH,
            organization=org,
            candidates=scored_matches,
            confidence=top_match.score,
            message=f"No confident match for {org.name} (best: {top_match.score:.2f})",
        )
