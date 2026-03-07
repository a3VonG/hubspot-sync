"""Matching logic for HubSpot-Platform sync."""

from .matcher import Matcher, MatchResult, MatchType
from .signals import SignalCollector, MatchSignal, SignalType
from .scorer import Scorer

__all__ = [
    "Matcher",
    "MatchResult",
    "MatchType",
    "SignalCollector",
    "MatchSignal",
    "SignalType",
    "Scorer",
]
