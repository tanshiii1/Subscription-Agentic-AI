"""Signal computation and the candidate gate. Pure functions, no LLM, no I/O.

Everything the LLM later "knows" about a subscription comes from here, so it
can reason about numbers but never invent them.
"""
import calendar
from datetime import date, timedelta
from typing import Optional

from agent import config
from agent.models import Candidate, Signals, Subscription

PERIODS_PER_YEAR = {"weekly": 52, "monthly": 12, "yearly": 1}


def add_months(d: date, months: int) -> date:
    """Add months, clamping the day (31 Jan + 1 month = 28/29 Feb)."""
    idx = d.month - 1 + months
    year, month = d.year + idx // 12, idx % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _next_charge(last: date, cadence: str) -> date:
    if cadence == "weekly":
        return last + timedelta(days=7)
    return add_months(last, 1 if cadence == "monthly" else 12)


def price_change_pct(sub: Subscription) -> float:
    """Latest price vs the price before it. 0 if the price never changed."""
    if len(sub.price_history) < 2:
        return 0.0
    prev, curr = sub.price_history[-2][1], sub.price_history[-1][1]
    return round((curr - prev) / prev * 100, 1)


def days_to_renewal(sub: Subscription, today: date) -> Optional[int]:
    """Days until the next expected charge (None for cancelled subscriptions)."""
    if sub.status != "active":
        return None
    nxt = _next_charge(sub.last_charge_date, sub.cadence)
    while nxt < today:  # data is stale: roll forward to the next future date
        nxt = _next_charge(nxt, sub.cadence)
    return (nxt - today).days


def compute_signals(sub: Subscription, today: date) -> Signals:
    return Signals(
        merchant=sub.merchant,
        price_change_pct=price_change_pct(sub),
        # Unknown usage stays None: "no data" is not the same as "unused".
        days_since_last_use=(today - sub.last_used_date).days if sub.last_used_date else None,
        yearly_cost_inr=round(sub.current_price_inr * PERIODS_PER_YEAR[sub.cadence], 2),
        days_to_renewal=days_to_renewal(sub, today),
    )


def candidate_reasons(sub: Subscription, s: Signals) -> list[str]:
    """Loose gate: why this subscription deserves the LLM's attention."""
    if sub.status != "active":
        return []
    reasons = []
    if s.price_change_pct >= config.CANDIDATE_PRICE_HIKE_PCT:
        reasons.append("price_increase")
    if s.days_since_last_use is not None and s.days_since_last_use >= config.CANDIDATE_INACTIVE_DAYS:
        reasons.append("low_usage")
    if (sub.cadence == "yearly" and s.days_to_renewal is not None
            and s.days_to_renewal <= config.RENEWAL_WINDOW_DAYS):
        reasons.append("renewal_soon")
    return reasons


def obvious_reasons(s: Signals) -> list[str]:
    """Strict level: hints for the LLM and the offline fallback."""
    reasons = []
    if s.price_change_pct >= config.OBVIOUS_PRICE_HIKE_PCT:
        reasons.append("steep_price_increase")
    if s.days_since_last_use is not None and s.days_since_last_use >= config.OBVIOUS_INACTIVE_DAYS:
        reasons.append("long_inactivity")
    return reasons


def fingerprint(c: Candidate, today: date) -> str:
    """Identity of an issue, built from the concrete facts behind it.

    Deliberately excludes things that change daily (like days idle), so the
    same unresolved issue yields the same fingerprint on every run, while a
    new price, new last-use date or new renewal date yields a new one.
    """
    parts = []
    for r in sorted(c.reasons):
        if r == "price_increase":
            parts.append(f"price:{c.sub.price_history[-2][1]:g}->{c.sub.current_price_inr:g}")
        elif r == "low_usage":
            parts.append(f"unused_since:{c.sub.last_used_date}")
        elif r == "renewal_soon":
            parts.append(f"renews:{today + timedelta(days=c.signals.days_to_renewal)}")
    return "|".join(parts)
