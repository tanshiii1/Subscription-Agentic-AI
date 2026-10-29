"""Logging: readable decisions on the console, timestamped copy in logs/."""
import logging

from agent import config

log = logging.getLogger("watchdog")


def setup_logging() -> None:
    if log.handlers:
        return
    config.LOG_DIR.mkdir(exist_ok=True)
    log.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    file = logging.FileHandler(config.LOG_DIR / "watchdog.log", encoding="utf-8")
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(console)
    log.addHandler(file)
