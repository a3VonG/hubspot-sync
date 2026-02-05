"""Matching logic for HubSpot-Platform sync."""

from matching.matcher import Matcher, MatchResult, MatchType
from matching.signals import SignalCollector, MatchSignal, SignalType
from matching.scorer import Scorer

__all__ = [
    "Matcher",
    "MatchResult",
    "MatchType",
    "SignalCollector",
    "MatchSignal",
    "SignalType",
    "Scorer",
]
