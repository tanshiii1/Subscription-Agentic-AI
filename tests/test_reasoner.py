import json

import groq
import httpx
import pytest

from agent.models import Candidate, Signals, Subscription
from agent.reasoner import (GroqLLM, LLMUnavailable, Reasoner,
                            ungrounded_numbers)
from datetime import date


def candidate(pct=60.1, idle=3, prices=(499.0, 799.0), cadence="monthly",
              reasons=("price_increase",), obvious=("steep_price_increase",)):
    sub = Subscription(merchant="StreamFlix", cadence=cadence, current_price_inr=prices[-1],
                       price_history=[(date(2026, 1, 1 + i), p) for i, p in enumerate(prices)],
                       last_charge_date=date(2026, 10, 5))
    sig = Signals(merchant="StreamFlix", price_change_pct=pct, days_since_last_use=idle,
                  yearly_cost_inr=9588.0, days_to_renewal=7)
    return Candidate(sub=sub, signals=sig, reasons=list(reasons), obvious=list(obvious))


def reply(alert=True, severity="high", reason="Price rose 60.1% to a yearly cost of ₹9,588."):
    return json.dumps({"alert": alert, "severity": severity, "reason": reason})


def scripted(*replies):
    it = iter(replies)
    def call(system, user):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    return call


def test_valid_reply_becomes_decision():
    d = Reasoner(scripted(reply())).decide(candidate())
    assert d.alert and d.severity == "high" and d.source == "llm"


def test_merchant_comes_from_code_not_llm():
    raw = json.dumps({"merchant": "Hacked", "alert": True, "severity": "low", "reason": "x"})
    assert Reasoner(scripted(raw)).decide(candidate()).merchant == "StreamFlix"


def test_bad_json_gets_one_correction_round():
    d = Reasoner(scripted("not json", reply())).decide(candidate())
    assert d.source == "llm"


def test_bad_json_twice_falls_back():
    d = Reasoner(scripted("nope", "still nope")).decide(candidate())
    assert d.source == "fallback" and "output invalid" in d.reason


def test_invalid_severity_rejected():
    bad = json.dumps({"alert": True, "severity": "catastrophic", "reason": "x"})
    assert Reasoner(scripted(bad, bad)).decide(candidate()).source == "fallback"


def test_invented_number_rejected_then_corrected():
    lie = reply(reason="Price rose 85% this month.")
    d = Reasoner(scripted(lie, reply())).decide(candidate())
    assert d.source == "llm" and "60.1" in d.reason


def test_invented_number_twice_falls_back():
    lie = reply(reason="Price rose 85%.")
    assert Reasoner(scripted(lie, lie)).decide(candidate()).source == "fallback"


def test_grounding_accepts_given_numbers_and_rounding():
    allowed = [60.1, 9588.0, 799.0, 499.0, 300.0, 138.0, 138 / 30.4]
    assert ungrounded_numbers("Up 60% to ₹799 from ₹499, ₹9,588 a year", allowed) == []
    assert ungrounded_numbers("unused for about 4.5 months", allowed) == []
    assert ungrounded_numbers("costs ₹12,000 a year", allowed) == ["12,000"]


def test_llm_unavailable_falls_back_with_obvious_alert():
    d = Reasoner(scripted(LLMUnavailable("down"))).decide(candidate())
    assert d.source == "fallback" and d.alert and "LLM unavailable" in d.reason


def test_llm_disabled_fallback_no_alert_below_strict():
    c = candidate(pct=6.7, prices=(149.0, 159.0), obvious=())
    d = Reasoner(None).decide(c)
    assert d.source == "fallback" and not d.alert


def test_fallback_renewal_only_is_low_alert():
    c = candidate(pct=0, prices=(1499.0,), cadence="yearly", reasons=("renewal_soon",), obvious=())
    d = Reasoner(None).decide(c)
    assert d.alert and d.severity == "low"


# --- GroqLLM retry behaviour (no network) ---
def http_error(cls, status):
    resp = httpx.Response(status, request=httpx.Request("POST", "http://x"))
    return cls("boom", response=resp, body=None)


class FakeChat:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return type("Msg", (), {"content": o})()


def test_groq_retries_rate_limit_then_succeeds():
    sleeps = []
    chat = FakeChat(http_error(groq.RateLimitError, 429), http_error(groq.RateLimitError, 429), "{}")
    out = GroqLLM(chat=chat, sleep=sleeps.append, max_retries=3)("s", "u")
    assert out == "{}" and chat.calls == 3 and sleeps == [1, 2]  # exponential backoff


def test_groq_gives_up_after_max_retries():
    chat = FakeChat(*[http_error(groq.InternalServerError, 500)] * 3)
    with pytest.raises(LLMUnavailable):
        GroqLLM(chat=chat, sleep=lambda s: None, max_retries=2)("s", "u")
    assert chat.calls == 3


def test_groq_auth_error_is_not_retried():
    chat = FakeChat(http_error(groq.AuthenticationError, 401), "{}")
    with pytest.raises(LLMUnavailable):
        GroqLLM(chat=chat, sleep=lambda s: None, max_retries=3)("s", "u")
    assert chat.calls == 1
