"""Rules-based creator-lead scoring (0..100) + tiering.

Deliberately transparent: every point is attributed in `breakdown` so the recruitment team
(and the GHL note) can see *why* a lead is hot. Weights are overridable per tenant via the
`scoring:` block in clients/<slug>.yaml.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

EARNINGS_POINTS: dict[str, int] = {
    "none": 0,
    "0-1k": 5,
    "1k-5k": 15,
    "5k-10k": 25,
    "10k-25k": 32,
    "25k+": 35,
}

DEFAULT_WEIGHTS: dict[str, float] = {
    "followers_max": 35,  # points for audience size (log scale, caps at followers_cap)
    "followers_cap": 1_000_000,
    "multi_platform": 4,  # per additional platform with >= 1k followers, max 3
    "has_phone": 5,
    "has_socials": 5,
    "timezone_match": 5,
}

DEFAULT_TIERS: dict[str, int] = {"hot": 70, "warm": 40}


@dataclass
class ScoreResult:
    score: int
    tier: str
    breakdown: dict[str, float] = field(default_factory=dict)


def follower_points(total_followers: int, max_points: float, cap: int) -> float:
    if total_followers <= 0:
        return 0.0
    total = min(total_followers, cap)
    return round(max_points * math.log10(total + 1) / math.log10(cap + 1), 2)


def tier_for(score: int, tiers: dict[str, int] | None = None) -> str:
    t = tiers or DEFAULT_TIERS
    if score >= t["hot"]:
        return "hot"
    if score >= t["warm"]:
        return "warm"
    return "cold"


def score_lead(
    *,
    followers: dict[str, int],
    earnings_range: str,
    has_phone: bool,
    socials: dict[str, str],
    timezone: str = "",
    target_timezones: list[str] | None = None,
    weights: dict[str, Any] | None = None,
    tiers: dict[str, int] | None = None,
    ai_adjustment: int = 0,
) -> ScoreResult:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    breakdown: dict[str, float] = {}
    clean = {k: max(0, int(v or 0)) for k, v in followers.items()}
    total = sum(clean.values())
    breakdown["audience"] = follower_points(total, float(w["followers_max"]), int(w["followers_cap"]))
    big_platforms = sum(1 for v in clean.values() if v >= 1000)
    breakdown["multi_platform"] = float(min(max(big_platforms - 1, 0), 3) * w["multi_platform"])
    breakdown["earnings"] = float(EARNINGS_POINTS.get(earnings_range, 0))
    breakdown["contactable"] = float(w["has_phone"]) if has_phone else 0.0
    breakdown["socials"] = float(w["has_socials"]) if any(v for v in socials.values()) else 0.0
    tz_ok = (
        bool(timezone)
        and bool(target_timezones)
        and any(timezone.startswith(prefix) for prefix in (target_timezones or []))
    )
    breakdown["timezone"] = float(w["timezone_match"]) if tz_ok else 0.0
    adj = max(-10, min(10, int(ai_adjustment)))
    if adj:
        breakdown["ai_adjustment"] = float(adj)
    raw = sum(breakdown.values())
    score = round(max(0.0, min(100.0, raw)))
    return ScoreResult(score=score, tier=tier_for(score, tiers), breakdown=breakdown)
