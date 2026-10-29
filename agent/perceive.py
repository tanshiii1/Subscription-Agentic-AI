"""Perceive step: turn raw billing data into Transaction objects.

Every source implements `Perceiver`, so a mock-email reader can be added
later without touching the rest of the agent.
"""
import csv
import re
from abc import ABC, abstractmethod
from datetime import date
from email import message_from_string
from email.utils import parsedate_to_datetime
from pathlib import Path

from agent import config
from agent.log import log
from agent.models import Transaction


class Perceiver(ABC):
    @abstractmethod
    def fetch(self) -> list[Transaction]:
        ...


class CsvPerceiver(Perceiver):
    """Reads date,merchant,amount,currency rows."""

    def __init__(self, path: Path):
        self.path = path

    def fetch(self) -> list[Transaction]:
        with open(self.path, newline="") as f:
            return [
                Transaction(
                    date=date.fromisoformat(r["date"]),
                    merchant=r["merchant"].strip(),
                    amount=float(r["amount"]),
                    currency=r["currency"].strip().upper(),
                )
                for r in csv.DictReader(f)
            ]


class EmailPerceiver(Perceiver):
    """Reads mock billing emails (.txt / .eml) from a folder.

    Expected: a Date header plus body lines `Merchant: <name>` and
    `Amount: <CUR> <number>`. Regex, not the LLM: amounts are facts.
    """
    _MERCHANT = re.compile(r"^Merchant:\s*(.+)$", re.MULTILINE)
    _AMOUNT = re.compile(r"^Amount:\s*([A-Za-z]{3})\s*([\d,]+(?:\.\d+)?)", re.MULTILINE)

    def __init__(self, folder: Path):
        self.folder = folder

    def fetch(self) -> list[Transaction]:
        txns = []
        for path in sorted(self.folder.glob("*")):
            if path.suffix.lower() not in (".txt", ".eml"):
                continue
            try:
                txns.append(self._parse(path.read_text(encoding="utf-8")))
            except ValueError as e:
                log.warning("  Skipping email %s: %s", path.name, e)
        return txns

    def _parse(self, text: str) -> Transaction:
        msg = message_from_string(text)
        header = (msg["Date"] or "").strip()
        try:
            when = date.fromisoformat(header)
        except ValueError:
            try:
                when = parsedate_to_datetime(header).date()
            except (TypeError, ValueError):
                raise ValueError(f"unreadable Date header {header!r}")
        body = msg.get_payload()
        m, a = self._MERCHANT.search(body), self._AMOUNT.search(body)
        if not (m and a):
            raise ValueError("missing Merchant: or Amount: line")
        currency = a.group(1).upper()
        if currency not in config.MOCK_FX_TO_INR:
            raise ValueError(f"unsupported currency {currency}")
        return Transaction(date=when, merchant=m.group(1).strip(),
                           amount=float(a.group(2).replace(",", "")), currency=currency)
