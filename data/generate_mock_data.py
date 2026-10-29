"""Generate reproducible demo data: data/transactions.csv and data/usage.csv.

Run:  python data/generate_mock_data.py [--seed 42]

Scenarios (relative to REFERENCE_DATE in agent/config.py):
  StreamFlix       monthly, steep hike (499 -> 799)           -> clear alert
  FitZone Gym      monthly, price stable, unused 4+ months    -> alert
  CloudDrive       monthly, small hike (149 -> 159)           -> no alert
  PrimeShow Annual yearly, renews in ~2 weeks                 -> heads-up
  Spotifree        monthly, stable, used daily                -> no alert
  StreamPlus       monthly, small hike (299 -> 329), unused ~3 months
                                                              -> grey zone, LLM judges
  CodeAssist       monthly, USD 10, stable                    -> currency handling
Plus one-off noise purchases that must NOT be detected as subscriptions.
"""
import argparse
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent import config  # noqa: E402


def months_back(ref: date, n: int, day: int) -> date:
    """Date `n` months before ref's month, on a fixed billing day (<=28)."""
    y, m = ref.year, ref.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, day)


def monthly_series(rng, ref, merchant, day, prices, currency="INR"):
    """`prices` is oldest-to-newest, one entry per month ending at/before ref."""
    rows = []
    n = len(prices)
    for i, price in enumerate(prices):
        d = months_back(ref, n - 1 - i, day) + timedelta(days=rng.randint(0, 1))
        if d <= ref:
            rows.append((d, merchant, price, currency))
    return rows


def build_transactions(rng, ref):
    rows = []
    rows += monthly_series(rng, ref, "StreamFlix", 5, [499] * 8 + [799] * 2)
    rows += monthly_series(rng, ref, "FitZone Gym", 10, [1500] * 10)
    rows += monthly_series(rng, ref, "CloudDrive", 18, [149] * 9 + [159] * 1)
    rows += monthly_series(rng, ref, "Spotifree", 2, [119] * 10)
    rows += monthly_series(rng, ref, "StreamPlus", 22, [299] * 8 + [329] * 2)
    rows += monthly_series(rng, ref, "CodeAssist", 14, [10.0] * 10, "USD")
    # Yearly: last charged ~351 days ago -> renews in ~14 days
    last = ref - timedelta(days=351)
    rows.append((last.replace(year=last.year - 2), "PrimeShow Annual", 1499, "INR"))
    rows.append((last.replace(year=last.year - 1), "PrimeShow Annual", 1499, "INR"))
    rows.append((last, "PrimeShow Annual", 1499, "INR"))
    # One-off noise: irregular amounts and gaps
    for merchant, lo, hi, k in [("Swiggy", 180, 650, 14), ("Amazon", 300, 4500, 6),
                                ("Uber", 90, 420, 9)]:
        for _ in range(k):
            d = ref - timedelta(days=rng.randint(0, 300))
            rows.append((d, merchant, round(rng.uniform(lo, hi), 2), "INR"))
    rows.sort(key=lambda r: r[0])
    return rows


def build_usage(rng, ref):
    """service,last_used_date  (replace with a real source later via UsageProvider)."""
    return [
        ("StreamFlix", ref - timedelta(days=rng.randint(1, 4))),
        ("FitZone Gym", ref - timedelta(days=rng.randint(135, 150))),
        ("CloudDrive", ref - timedelta(days=rng.randint(1, 6))),
        ("PrimeShow Annual", ref - timedelta(days=rng.randint(10, 25))),
        ("Spotifree", ref - timedelta(days=rng.randint(0, 2))),
        ("StreamPlus", ref - timedelta(days=rng.randint(88, 98))),
        ("CodeAssist", ref - timedelta(days=rng.randint(0, 3))),
    ]


def write_emails(ref):
    """4 monthly billing emails for a service that exists ONLY in the inbox
    (NewsDaily, no usage data) to demo the second perceiver."""
    config.INBOX_DIR.mkdir(exist_ok=True)
    for old in config.INBOX_DIR.glob("*"):
        old.unlink()
    for i, n in enumerate(range(3, -1, -1)):
        d = months_back(ref, n, 9)
        ext = "eml" if i == 3 else "txt"
        (config.INBOX_DIR / f"newsdaily_{d.isoformat()}.{ext}").write_text(
            "From: billing@newsdaily.example\n"
            f"Date: {d.isoformat()}\n"
            "Subject: Your NewsDaily receipt\n\n"
            "Thanks for subscribing! We charged your card for NewsDaily Premium.\n"
            "Merchant: NewsDaily\n"
            "Amount: INR 199.00\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--today", type=date.fromisoformat, default=config.REFERENCE_DATE)
    args = p.parse_args()

    rng = random.Random(args.seed)
    config.DATA_DIR.mkdir(exist_ok=True)

    txns = build_transactions(rng, args.today)
    with open(config.TRANSACTIONS_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "merchant", "amount", "currency"])
        for d, m, a, c in txns:
            w.writerow([d.isoformat(), m, a, c])

    usage = build_usage(rng, args.today)
    with open(config.USAGE_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["service", "last_used_date"])
        for s, d in usage:
            w.writerow([s, d.isoformat()])

    print(f"Wrote {len(txns)} transactions -> {config.TRANSACTIONS_CSV}")
    print(f"Wrote {len(usage)} usage rows   -> {config.USAGE_CSV}")
    write_emails(args.today)
    print(f"Wrote 4 billing emails  -> {config.INBOX_DIR}")


if __name__ == "__main__":
    main()
