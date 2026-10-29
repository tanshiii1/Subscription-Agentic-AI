"""Notification channels. Add email / desktop by subclassing `Notifier`."""
import json
from datetime import datetime
from abc import ABC, abstractmethod
from pathlib import Path

from agent.models import AlertDecision, Signals


class Notifier(ABC):
    @abstractmethod
    def send(self, decision: AlertDecision, signals: Signals) -> None:
        """Deliver one alert. Raise on failure so the agent retries next run."""


class ConsoleNotifier(Notifier):
    def send(self, decision: AlertDecision, signals: Signals) -> None:
        print(f"\n🔔 SUBSCRIPTION ALERT [{decision.severity.upper()}] {decision.merchant}\n"
              f"   {decision.reason}\n")


class FileNotifier(Notifier):
    """Appends one JSON object per alert (easy to grep, easy to load later)."""

    def __init__(self, path: Path):
        self.path = path

    def send(self, decision: AlertDecision, signals: Signals) -> None:
        self.path.parent.mkdir(exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"sent_at": datetime.now().isoformat(timespec="seconds"),
                                "decision": decision.model_dump(),
                                "signals": signals.model_dump()}, default=str) + "\n")


class MultiNotifier(Notifier):
    """Fan out to several channels. All must succeed, else the alert is retried."""

    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, decision: AlertDecision, signals: Signals) -> None:
        for n in self.notifiers:
            n.send(decision, signals)
