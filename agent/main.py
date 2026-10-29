"""Entry point.

  python -m agent.main --once          one pass, then exit
  python -m agent.main                 poll forever (re-run when data/date changes)
"""
import argparse
from datetime import date

from agent import config
from agent.graph import build_graph
from agent.log import log, setup_logging
from agent.loop import watch
from agent.notifier import ConsoleNotifier, FileNotifier, MultiNotifier
from agent.perceive import EmailPerceiver
from agent.reasoner import make_reasoner


def main() -> None:
    p = argparse.ArgumentParser(description="Subscription Watchdog Agent")
    p.add_argument("--today", type=date.fromisoformat, default=config.REFERENCE_DATE,
                   help="reference date (default: fixed demo date in config)")
    p.add_argument("--real-date", action="store_true", help="use the system clock instead")
    p.add_argument("--once", action="store_true", help="run a single pass and exit")
    p.add_argument("--interval", type=int, default=config.POLL_INTERVAL_SECONDS,
                   help="seconds between polls in loop mode")
    p.add_argument("--no-llm", action="store_true", help="skip Groq; rule-based fallback only")
    p.add_argument("--inbox", action="store_true", help="also read billing emails from data/inbox")
    args = p.parse_args()

    setup_logging()
    extras = (EmailPerceiver(config.INBOX_DIR),) if args.inbox else ()
    graph = build_graph(make_reasoner(not args.no_llm),
                        MultiNotifier([ConsoleNotifier(), FileNotifier(config.ALERT_LOG)]),
                        extra_perceivers=extras)

    def run(today: date) -> None:
        log.info("=== Watchdog run, reference date %s ===", today)
        graph.invoke({"today": today})

    today_fn = date.today if args.real_date else (lambda: args.today)
    if args.once:
        run(today_fn())
        return
    inputs = lambda: [config.TRANSACTIONS_CSV, config.USAGE_CSV,
                      *(sorted(config.INBOX_DIR.glob("*")) if args.inbox else [])]
    log.info("Watching for changes every %ds (Ctrl-C to stop)", args.interval)
    try:
        watch(run, inputs, today_fn, args.interval)
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
