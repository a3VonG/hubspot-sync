"""
Scoring and confidence calculation for company matches.

Combines multiple signals to determine overall match confidence.
"""

from dataclasses import dataclass
from typing import Optional

from matching.signals import MatchSignal, SignalType
from clients.hubspot import Company


@dataclass
class ScoredMatch:
    """A company match with aggregated score."""
    company: Company
    score: float
    signals: list[MatchSignal]
    is_ground_truth: bool = False
    has_conflict: bool = False
    
    @property
    def signal_types(self) -> list[SignalType]:
        """Get the types of signals contributing to this match."""
        return [s.signal_type for s in self.signals]


class Scorer:
    """
    Aggregates signals and calculates match scores.
    
    Multiple signals for the same company are combined to produce
    a single confidence score.
    """
    
    # Signal type weights for combining multiple signals
    SIGNAL_WEIGHTS = {
        SignalType.EXISTING_PLATFORM_ID: 1.0,  # Ground truth
        SignalType.DOMAIN_MATCH: 0.4,
        SignalType.CONTACT_ASSOCIATION: 0.35,
        SignalType.PADDLE_NAME_MATCH: 0.25,
        SignalType.PADDLE_VAT_MATCH: 0.3,
    }
    
    def score_signals(
        self, 
        signals: list[MatchSignal],
        platform_org_id: str,
    ) -> list[ScoredMatch]:
        """
        Aggregate signals by company and calculate scores.
        
        Args:
            signals: List of all signals collected
            platform_org_id: The platform org ID we're matching
            
        Returns:
            List of ScoredMatch objects, sorted by score descending
        """
        # Group signals by company
        company_signals: dict[str, list[MatchSignal]] = {}
        company_objects: dict[str, Company] = {}
        
        for signal in signals:
            company_id = signal.company.id
            if company_id not in company_signals:
                company_signals[company_id] = []
                company_objects[company_id] = signal.company
            company_signals[company_id].append(signal)
        
        # Calculate score for each company
        scored_matches = []
        for company_id, sigs in company_signals.items():
            company = company_objects[company_id]
            
            # Check for ground truth (already linked)
            is_ground_truth = any(
                s.signal_type == SignalType.EXISTING_PLATFORM_ID
                for s in sigs
            )
            
            # Check for conflict (different platform ID already set)
            has_conflict = (
                company.platform_org_id is not None 
                and company.platform_org_id != platform_org_id
            )
            
            # Calculate combined score
            if is_ground_truth:
                score = 1.0
            else:
                score = self._calculate_combined_score(sigs)
                if has_conflict:
                    score *= 0.3  # Heavily penalize conflicts
            
            scored_matches.append(ScoredMatch(
                company=company,
                score=score,
                signals=sigs,
                is_ground_truth=is_ground_truth,
                has_conflict=has_conflict,
            ))
        
        # Sort by score descending
        scored_matches.sort(key=lambda m: m.score, reverse=True)
        
        return scored_matches
    
    def _calculate_combined_score(self, signals: list[MatchSignal]) -> float:
        """
        Calculate combined confidence score from multiple signals.
        
        Uses weighted average with boost for multiple corroborating signals.
        """
        if not signals:
            return 0.0
        
        # Calculate weighted average of signal confidences
        total_weight = 0.0
        weighted_sum = 0.0
        
        for signal in signals:
            weight = self.SIGNAL_WEIGHTS.get(signal.signal_type, 0.2)
            weighted_sum += signal.confidence * weight
            total_weight += weight
        
        base_score = weighted_sum / total_weight if total_weight > 0 else 0.0
        
        # Boost for multiple corroborating signals
        unique_signal_types = len(set(s.signal_type for s in signals))
        if unique_signal_types >= 2:
            boost = min(0.15, 0.05 * unique_signal_types)
            base_score = min(0.95, base_score + boost)
        
        return round(base_score, 3)
