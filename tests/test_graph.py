import json

from agent.graph import build_graph
from agent.reasoner import LLMUnavailable, Reasoner
from agent import config


def run(reasoner, tmp_path, notifier=None):
    g = build_graph(reasoner, notifier, db_path=tmp_path / "t.db")
    return g.invoke({"today": config.REFERENCE_DATE})


def always_alert(system, user):
    return json.dumps({"alert": True, "severity": "medium", "reason": "Worth a look."})


def test_only_candidates_reach_the_llm(tmp_path):
    calls = []
    def llm(system, user):
        calls.append(user)
        return always_alert(system, user)
    state = run(Reasoner(llm), tmp_path)
    assert len(calls) == len(state["candidates"]) == 5
    assert sorted(state["skipped"]) == ["CodeAssist", "Spotifree"]
    assert {d.merchant for d in state["decisions"]} == {c.sub.merchant for c in state["candidates"]}


def test_prompt_contains_facts_not_raw_transactions(tmp_path):
    seen = []
    run(Reasoner(lambda s, u: seen.append(u) or always_alert(s, u)), tmp_path)
    assert any('"price_change_pct": 60.1' in u for u in seen)
    assert not any("Swiggy" in u for u in seen)


def test_llm_outage_degrades_to_rule_based(tmp_path):
    def down(system, user):
        raise LLMUnavailable("down")
    state = run(Reasoner(down), tmp_path)
    by = {d.merchant: d for d in state["decisions"]}
    assert all(d.source == "fallback" for d in by.values())
    assert by["StreamFlix"].alert and by["FitZone Gym"].alert
    assert not by["CloudDrive"].alert and not by["StreamPlus"].alert


def test_registry_is_populated(tmp_path):
    run(Reasoner(None), tmp_path)
    from agent.registry import Registry
    assert len(Registry(tmp_path / "t.db").get_all()) == 7
