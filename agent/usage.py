"""Usage signal source. Swap CsvUsageProvider for a real one (app API,
screen-time export, gym check-ins) by implementing `UsageProvider`."""
import csv
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Optional


class UsageProvider(ABC):
    @abstractmethod
    def last_used(self, service: str) -> Optional[date]:
        ...


class CsvUsageProvider(UsageProvider):
    def __init__(self, path: Path):
        with open(path, newline="") as f:
            self._data = {
                r["service"].strip().lower(): date.fromisoformat(r["last_used_date"])
                for r in csv.DictReader(f)
            }

    def last_used(self, service: str) -> Optional[date]:
        return self._data.get(service.strip().lower())
