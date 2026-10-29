"""The Reason node's brain: ask the LLM for a judgment, never for facts.

Defences against LLM trouble, in order:
  1. Groq errors / rate limits  -> exponential backoff retries (GroqLLM)
  2. Still failing              -> LLMUnavailable -> rule-based fallback
  3. Malformed JSON / schema    -> one correction round, then fallback
  4. Reason cites a number that is not in the supplied signals
     (hallucination)            -> same as 3
"""
import json
import re
import time
from typing import Callable, Optional

import groq

from agent import config
from agent.log import log
from agent.models import AlertDecision, Candidate

# llm_call(system_prompt, user_prompt) -> raw text. Swappable for tests.
LLMCall = Callable[[str, str], str]


class LLMUnavailable(Exception):
    """Raised when the LLM cannot be reached after all retries."""


SYSTEM_PROMPT = """You are the judgment module of a subscription watchdog agent.
You receive FACTS about one subscription, already computed by code. Decide
whether the user should be alerted now.

Rules:
- Alert only when something genuinely deserves attention. Alert fatigue is a
  failure: a small price rise on a well-used service is NOT worth an alert.
- Weigh signals together. A modest price rise on a service unused for months
  is worth more attention than either signal alone.
- Use ONLY the numbers given in the facts. Never compute, estimate or invent
  numbers, and never quote dates. Copy values exactly.
- severity: "high" = act soon, "medium" = worth a look, "low" = FYI.
  If alert is false, use severity "low".
- reason: ONE sentence the user could read in a notification.

Reply with a single JSON object and nothing else:
{"alert": true|false, "severity": "low"|"medium"|"high", "reason": "<one sentence>"}"""


def build_user_prompt(c: Candidate) -> str:
    s, sub = c.signals, c.sub
    prev = sub.price_history[-2][1] if len(sub.price_history) > 1 else None
    facts = {
        "merchant": sub.merchant,
        "category": sub.category,
        "billing_cadence": sub.cadence,
        "current_price_inr": sub.current_price_inr,
        "previous_price_inr": prev,
        "price_change_pct": s.price_change_pct,
        "days_since_last_use": s.days_since_last_use,
        "yearly_cost_inr": s.yearly_cost_inr,
        "days_to_next_renewal": s.days_to_renewal,
        "why_flagged_for_review": c.reasons,
        "strict_level_flags": c.obvious,
    }
    return "FACTS:\n" + json.dumps(facts, indent=2)


# --- guard against invented numbers ---------------------------------------
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def allowed_numbers(c: Candidate) -> list[float]:
    s, sub = c.signals, c.sub
    nums = [abs(s.price_change_pct), s.yearly_cost_inr, sub.current_price_inr]
    if len(sub.price_history) > 1:
        prev = sub.price_history[-2][1]
        nums += [prev, sub.current_price_inr - prev]
    if s.days_since_last_use is not None:
        nums += [s.days_since_last_use, s.days_since_last_use / 30.4]  # "about 4 months"
    if s.days_to_renewal is not None:
        nums.append(s.days_to_renewal)
    return nums


def ungrounded_numbers(text: str, allowed: list[float]) -> list[str]:
    bad = []
    for tok in _NUM.findall(text):
        v = float(tok.replace(",", ""))
        if not any(abs(v - a) <= max(0.5, 0.01 * abs(a)) for a in allowed):
            bad.append(tok)
    return bad


# --- rule-based fallback ---------------------------------------------------
def rule_based_decision(c: Candidate, note: str) -> AlertDecision:
    """Used only when the LLM is unavailable. Facts-only, no judgment."""
    s = c.signals
    parts = []
    if "steep_price_increase" in c.obvious:
        parts.append(f"price up {s.price_change_pct:g}%")
    if "long_inactivity" in c.obvious:
        parts.append(f"unused for {s.days_since_last_use} days")
    if c.obvious:
        severity = "high" if len(c.obvious) > 1 else "medium"
        alert = True
    elif "renewal_soon" in c.reasons:
        parts.append(f"renews in {s.days_to_renewal} days (₹{s.yearly_cost_inr:g}/year)")
        severity, alert = "low", True
    else:
        parts.append("below strict thresholds; no alert without LLM judgment")
        severity, alert = "low", False
    return AlertDecision(merchant=c.sub.merchant, alert=alert, severity=severity,
                         reason=f"[rule-based fallback: {note}] " + "; ".join(parts),
                         source="fallback")


# --- the reasoner ----------------------------------------------------------
class Reasoner:
    def __init__(self, llm_call: Optional[LLMCall]):
        """llm_call=None means LLM disabled: every decision uses the fallback."""
        self.llm_call = llm_call

    def decide(self, c: Candidate) -> AlertDecision:
        if self.llm_call is None:
            return rule_based_decision(c, "LLM disabled")
        prompt = build_user_prompt(c)
        allowed = allowed_numbers(c)
        for attempt in (1, 2):  # second attempt = one correction round
            try:
                raw = self.llm_call(SYSTEM_PROMPT, prompt)
            except LLMUnavailable as e:
                log.warning("  LLM unavailable for %s: %s", c.sub.merchant, e)
                return rule_based_decision(c, "LLM unavailable")
            try:
                decision = self._parse(raw, c, allowed)
                return decision
            except ValueError as e:
                log.warning("  Bad LLM output for %s (attempt %d): %s", c.sub.merchant, attempt, e)
                prompt += f"\n\nYour previous reply was rejected: {e}. Reply again with valid JSON."
        return rule_based_decision(c, "LLM output invalid")

    @staticmethod
    def _parse(raw: str, c: Candidate, allowed: list[float]) -> AlertDecision:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("JSON is not an object")
            data["merchant"] = c.sub.merchant  # code owns identity, not the LLM
            decision = AlertDecision.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"not valid per schema ({str(e)[:120]})") from e
        bad = ungrounded_numbers(decision.reason, allowed)
        if bad:
            raise ValueError(f"reason contains numbers not in the facts: {bad}")
        return decision


# --- Groq client -----------------------------------------------------------
_RETRYABLE = (groq.RateLimitError, groq.APIConnectionError, groq.InternalServerError)


class GroqLLM:
    """Callable LLM client with exponential backoff on transient errors."""

    def __init__(self, api_key: str = config.GROQ_API_KEY, model: str = config.GROQ_MODEL,
                 max_retries: int = config.LLM_MAX_RETRIES, sleep=time.sleep, chat=None):
        self.max_retries, self._sleep = max_retries, sleep
        if chat is None:
            from langchain_groq import ChatGroq
            chat = ChatGroq(model=model, api_key=api_key, temperature=0, max_retries=0,
                            timeout=30, model_kwargs={"response_format": {"type": "json_object"}})
        self._chat = chat

    def __call__(self, system: str, user: str) -> str:
        for attempt in range(self.max_retries + 1):
            try:
                return self._chat.invoke([("system", system), ("user", user)]).content
            except _RETRYABLE as e:
                if attempt == self.max_retries:
                    raise LLMUnavailable(f"{type(e).__name__} after {attempt + 1} tries") from e
                wait = self._backoff(e, attempt)
                log.warning("  Groq %s, retry %d/%d in %.0fs", type(e).__name__,
                            attempt + 1, self.max_retries, wait)
                self._sleep(wait)
            except groq.APIError as e:  # auth, bad request, etc: retrying won't help
                raise LLMUnavailable(f"{type(e).__name__}: {str(e)[:80]}") from e
        raise LLMUnavailable("exhausted retries")  # pragma: no cover

    @staticmethod
    def _backoff(e: Exception, attempt: int) -> float:
        headers = getattr(getattr(e, "response", None), "headers", None) or {}
        try:
            return min(float(headers.get("retry-after", 0)) or 2 ** attempt, 60)
        except ValueError:
            return 2 ** attempt


def make_reasoner(use_llm: bool = True) -> Reasoner:
    if not use_llm:
        return Reasoner(None)
    if not config.GROQ_API_KEY:
        log.warning("GROQ_API_KEY not set in .env: using rule-based fallback for all decisions")
        return Reasoner(None)
    return Reasoner(GroqLLM())
