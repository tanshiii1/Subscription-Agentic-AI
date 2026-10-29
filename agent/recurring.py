"""Deterministic recurring-charge detection. No LLM involved.

Idea: a subscription is a merchant whose charges arrive at a regular gap.
We measure the gaps between consecutive charges and check whether most of
them fall inside a window for weekly / monthly / yearly billing.
"""
from collections import defaultdict
from datetime import date
from typing import Optional

from agent import config
from agent.models import Cadence, Subscription, Transaction

# (min_days, max_days) between consecutive charges. Monthly is wide because
# months differ in length and billing can drift by a day or two.
CADENCE_WINDOWS: dict[str, tuple[int, int]] = {
    "weekly": (5, 9),
    "monthly": (25, 36),
    "yearly": (350, 380),
}
# Charges needed before we trust a pattern (yearly has so little history).
MIN_CHARGES = {"weekly": 3, "monthly": 3, "yearly": 2}
MIN_REGULAR_GAP_FRACTION = 0.8  # share of gaps that must fit the window
MAX_PRICE_RATIO = 3.0           # real subscriptions don't triple in price


def to_inr(txn: Transaction) -> float:
    return round(txn.amount * config.MOCK_FX_TO_INR[txn.currency], 2)


def detect_cadence(dates: list[date]) -> Optional[Cadence]:
    """Return the cadence these (sorted) dates follow, or None."""
    for name, (lo, hi) in CADENCE_WINDOWS.items():
        if len(dates) < MIN_CHARGES[name]:
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        regular = sum(lo <= g <= hi for g in gaps)
        if regular / len(gaps) >= MIN_REGULAR_GAP_FRACTION:
            return name  # type: ignore[return-value]
    return None


def _price_history(charges: list[Transaction]) -> list[tuple[date, float]]:
    """Only the points where the price changed (first charge counts)."""
    history: list[tuple[date, float]] = []
    for t in charges:
        price = to_inr(t)
        if not history or abs(history[-1][1] - price) > 0.01:
            history.append((t.date, price))
    return history


def detect_subscriptions(txns: list[Transaction], today: date) -> list[Subscription]:
    by_merchant: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        if t.date <= today:  # ignore future-dated rows
            by_merchant[t.merchant.lower()].append(t)

    subs: list[Subscription] = []
    for charges in by_merchant.values():
        charges.sort(key=lambda t: t.date)
        cadence = detect_cadence([t.date for t in charges])
        if cadence is None:
            continue
        prices = [to_inr(t) for t in charges]
        if max(prices) / min(prices) > MAX_PRICE_RATIO:
            continue  # wildly varying amounts: shopping, not a subscription
        last = charges[-1]
        lapsed = (today - last.date).days > 2 * CADENCE_WINDOWS[cadence][1]
        subs.append(Subscription(
            merchant=charges[0].merchant,
            category=config.MERCHANT_CATEGORIES.get(charges[0].merchant.lower(), "other"),
            cadence=cadence,
            current_price_inr=prices[-1],
            price_history=_price_history(charges),
            last_charge_date=last.date,
            status="cancelled" if lapsed else "active",
        ))
    return sorted(subs, key=lambda s: s.merchant.lower())
