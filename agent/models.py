"""Typed data shapes shared by every node in the agent."""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Cadence = Literal["weekly", "monthly", "yearly"]


class Transaction(BaseModel):
    """One raw charge, as extracted by the perceive step."""
    date: date
    merchant: str
    amount: float
    currency: str = "INR"


class Subscription(BaseModel):
    """A detected recurring charge, as stored in the registry."""
    merchant: str
    category: str = "other"
    cadence: Cadence
    current_price_inr: float
    price_history: list[tuple[date, float]] = Field(default_factory=list)
    last_charge_date: date
    last_used_date: Optional[date] = None
    status: Literal["active", "cancelled"] = "active"


class Signals(BaseModel):
    """Facts computed in code. The LLM only ever sees these, never raw math."""
    merchant: str
    price_change_pct: float
    days_since_last_use: Optional[int]
    yearly_cost_inr: float
    days_to_renewal: Optional[int]


class AlertDecision(BaseModel):
    """The LLM's structured judgment."""
    merchant: str
    alert: bool
    severity: Literal["low", "medium", "high"]
    reason: str = Field(description="One line, referencing only provided signals")
    source: Literal["llm", "fallback"] = "llm"


class Candidate(BaseModel):
    """A subscription that passed the loose gate, bundled with its facts."""
    sub: Subscription
    signals: Signals
    reasons: list[str]   # why it passed the gate
    obvious: list[str]   # strict-level flags (hints, not verdicts)


class Evaluation(BaseModel):
    """Memory of the last review of a subscription; drives duplicate suppression."""
    merchant: str
    fingerprint: str   # the concrete facts behind the issue (see signals.fingerprint)
    source: Literal["llm", "fallback"]
    alerted: bool      # did the decision say "alert"?
    notified: bool     # was a notification actually delivered?
