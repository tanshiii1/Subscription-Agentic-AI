"""Polling loop: re-run the agent only when its inputs (or the date) changed."""
import time
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from agent.log import log


def data_signature(paths: list[Path], today: date) -> tuple:
    """Modified-times of every input file plus today's date."""
    mtimes = tuple(p.stat().st_mtime_ns if p.exists() else None for p in paths)
    return (mtimes, today)


def watch(run: Callable[[date], None], paths_fn: Callable[[], list[Path]],
          today_fn: Callable[[], date], interval: int, sleep=time.sleep,
          max_ticks: Optional[int] = None) -> None:
    last = None
    ticks = 0
    while True:
        today = today_fn()
        sig = data_signature(paths_fn(), today)
        if sig != last:
            try:
                run(today)
                last = sig  # only remember it if the run succeeded
            except Exception:
                log.exception("Run failed; will retry next tick")
        else:
            log.info("No new data since last run; sleeping %ds", interval)
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            return
        sleep(interval)
