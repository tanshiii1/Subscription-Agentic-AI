from datetime import date

from agent.models import Signals, Subscription
from agent.signals import (add_months, candidate_reasons, compute_signals,
                           obvious_reasons)

TODAY = date(2026, 10, 29)


def sub(prices=(499.0, 799.0), cadence="monthly", last_charge=date(2026, 10, 5),
        last_used=date(2026, 10, 26), status="active"):
    hist = [(date(2026, 1, 1 + i), p) for i, p in enumerate(prices)]
    return Subscription(merchant="Acme", cadence=cadence, current_price_inr=prices[-1],
                        price_history=hist, last_charge_date=last_charge,
                        last_used_date=last_used, status=status)


def test_price_change_pct():
    assert compute_signals(sub((499, 799)), TODAY).price_change_pct == 60.1


def test_no_price_change_is_zero():
    assert compute_signals(sub((100,)), TODAY).price_change_pct == 0.0


def test_price_decrease_is_negative():
    assert compute_signals(sub((200, 150)), TODAY).price_change_pct == -25.0


def test_yearly_cost_by_cadence():
    assert compute_signals(sub((100,), "monthly"), TODAY).yearly_cost_inr == 1200
    assert compute_signals(sub((100,), "weekly"), TODAY).yearly_cost_inr == 5200
    assert compute_signals(sub((1499,), "yearly"), TODAY).yearly_cost_inr == 1499


def test_days_since_last_use():
    s = sub(last_used=date(2026, 6, 13))
    assert compute_signals(s, TODAY).days_since_last_use == 138


def test_unknown_usage_is_none_not_zero():
    s = sub(last_used=None)
    sig = compute_signals(s, TODAY)
    assert sig.days_since_last_use is None
    assert "low_usage" not in candidate_reasons(s, sig)


def test_yearly_renewal_days():
    s = sub((1499,), "yearly", last_charge=date(2025, 11, 12))
    assert compute_signals(s, TODAY).days_to_renewal == 14


def test_stale_monthly_rolls_forward():
    s = sub((100,), "monthly", last_charge=date(2026, 7, 5))
    assert compute_signals(s, TODAY).days_to_renewal == 7  # next due 5 Nov


def test_cancelled_has_no_renewal_and_no_candidacy():
    s = sub(status="cancelled")
    sig = compute_signals(s, TODAY)
    assert sig.days_to_renewal is None
    assert candidate_reasons(s, sig) == []


def test_add_months_clamps_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)


def test_gate_small_hike_alone_is_not_obvious_but_is_candidate():
    s = sub((299, 329))  # +10%
    sig = compute_signals(s, TODAY)
    assert candidate_reasons(s, sig) == ["price_increase"]
    assert obvious_reasons(sig) == []


def test_gate_grey_zone_combines_signals():
    s = sub((299, 329), last_used=date(2026, 7, 26))  # +10%, 95 days idle
    sig = compute_signals(s, TODAY)
    assert candidate_reasons(s, sig) == ["price_increase", "low_usage"]
    assert obvious_reasons(sig) == []


def test_gate_stable_and_used_is_skipped():
    s = sub((119,), last_used=date(2026, 10, 29))
    assert candidate_reasons(s, compute_signals(s, TODAY)) == []


def test_gate_below_threshold_is_skipped():
    s = sub((100, 103))  # +3%
    assert candidate_reasons(s, compute_signals(s, TODAY)) == []


def test_obvious_flags():
    sig = Signals(merchant="x", price_change_pct=60, days_since_last_use=140,
                  yearly_cost_inr=1, days_to_renewal=None)
    assert obvious_reasons(sig) == ["steep_price_increase", "long_inactivity"]


def test_renewal_soon_only_for_yearly():
    y = sub((1499,), "yearly", last_charge=date(2025, 11, 12))
    assert "renewal_soon" in candidate_reasons(y, compute_signals(y, TODAY))
    m = sub((100,), "monthly", last_charge=date(2026, 10, 20))
    assert "renewal_soon" not in candidate_reasons(m, compute_signals(m, TODAY))


def test_fingerprint_stable_across_days_but_changes_with_facts():
    from agent.models import Candidate
    from agent.signals import fingerprint
    s = sub((299, 329), last_used=date(2026, 7, 26))
    def fp(today, subscription=s):
        sig = compute_signals(subscription, today)
        return fingerprint(Candidate(sub=subscription, signals=sig,
                                     reasons=candidate_reasons(subscription, sig), obvious=[]), today)
    assert fp(date(2026, 10, 29)) == fp(date(2026, 10, 30))      # days idle ticked up: same issue
    s2 = sub((299, 399), last_used=date(2026, 7, 26))
    assert fp(date(2026, 10, 29)) != fp(date(2026, 10, 29), s2)  # new price: new issue
