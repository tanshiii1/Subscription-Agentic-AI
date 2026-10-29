from datetime import date, timedelta

from agent.models import Transaction
from agent.recurring import detect_cadence, detect_subscriptions

TODAY = date(2026, 10, 29)


def tx(d, merchant="Acme", amount=100.0, currency="INR"):
    return Transaction(date=d, merchant=merchant, amount=amount, currency=currency)


def series(merchant, start, gap_days, n, amount=100.0, **kw):
    return [tx(start + timedelta(days=gap_days * i), merchant, amount, **kw) for i in range(n)]


def test_monthly_detected():
    subs = detect_subscriptions(series("Acme", date(2026, 5, 1), 30, 6), TODAY)
    assert [(s.merchant, s.cadence) for s in subs] == [("Acme", "monthly")]


def test_weekly_detected():
    assert detect_cadence([date(2026, 10, 1) + timedelta(days=7 * i) for i in range(4)]) == "weekly"


def test_yearly_needs_only_two_charges():
    assert detect_cadence([date(2025, 11, 12), date(2026, 11, 12)]) == "yearly"


def test_monthly_needs_three_charges():
    assert detect_cadence([date(2026, 8, 1), date(2026, 9, 1)]) is None


def test_irregular_gaps_rejected():
    dates = [date(2026, 1, 1), date(2026, 1, 9), date(2026, 3, 20), date(2026, 4, 2), date(2026, 8, 30)]
    assert detect_cadence(dates) is None


def test_one_missed_month_still_monthly():
    # 5 gaps, one of 60 days: 4/5 = 80% regular, right at the threshold
    dates = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 3), date(2026, 5, 2),
             date(2026, 6, 2), date(2026, 7, 2)]
    assert detect_cadence(dates) == "monthly"


def test_wildly_varying_amounts_rejected():
    txns = [tx(date(2026, 6, 1) + timedelta(days=30 * i), "Shop", a)
            for i, a in enumerate([100, 900, 150, 80, 700])]
    assert detect_subscriptions(txns, TODAY) == []


def test_price_history_keeps_only_changes():
    amounts = [499, 499, 499, 799, 799]
    txns = [tx(date(2026, 6, 1) + timedelta(days=30 * i), "Flix", a) for i, a in enumerate(amounts)]
    sub = detect_subscriptions(txns, TODAY)[0]
    assert [p for _, p in sub.price_history] == [499, 799]
    assert sub.current_price_inr == 799


def test_usd_converted_to_inr():
    txns = series("Code", date(2026, 6, 1), 30, 4, amount=10.0, currency="USD")
    assert detect_subscriptions(txns, TODAY)[0].current_price_inr == 850.0


def test_lapsed_subscription_marked_cancelled():
    txns = series("Old", date(2025, 1, 1), 30, 5)  # last charge ~ May 2025
    assert detect_subscriptions(txns, TODAY)[0].status == "cancelled"


def test_future_dated_rows_ignored():
    txns = series("Acme", date(2026, 8, 1), 30, 2) + [tx(date(2026, 12, 1), "Acme")]
    assert detect_subscriptions(txns, TODAY) == []  # only 2 past charges
