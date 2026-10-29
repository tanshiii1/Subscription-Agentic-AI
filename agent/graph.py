"""The agent as a LangGraph state graph: perceive -> reason -> act.

    perceive -> detect_recurring -> update_registry -> signals_and_gate
                                                          |
                                      candidates? --yes--> reason --> act
                                                  --no-----------------^

Duplicate-alert control lives in two places:
  * reason: if an issue's fingerprint is unchanged since the last LLM review,
    skip the LLM entirely (saves quota, and the answer would be the same).
  * act: never notify twice for the same fingerprint, even if the LLM
    (or the fallback) says "alert" again.
"""
from datetime import date
from pathlib import Path
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from agent import config
from agent.log import log
from agent.models import AlertDecision, Candidate, Evaluation, Subscription, Transaction
from agent.notifier import Notifier
from agent.perceive import CsvPerceiver, Perceiver
from agent.reasoner import Reasoner
from agent.recurring import detect_subscriptions
from agent.registry import Registry
from agent.signals import candidate_reasons, compute_signals, fingerprint, obvious_reasons
from agent.usage import CsvUsageProvider


class State(TypedDict, total=False):
    today: date
    txns: list[Transaction]
    subs: list[Subscription]
    candidates: list[Candidate]
    skipped: list[str]
    fingerprints: dict[str, str]
    unchanged: list[str]
    decisions: list[AlertDecision]


def build_graph(reasoner: Reasoner, notifier: Optional[Notifier] = None,
                db_path: Path = config.DB_PATH, txn_path: Path = config.TRANSACTIONS_CSV,
                usage_path: Path = config.USAGE_CSV,
                extra_perceivers: tuple[Perceiver, ...] = ()):
    registry = Registry(db_path)
    sources: list[Perceiver] = [CsvPerceiver(txn_path), *extra_perceivers]

    def perceive(state: State) -> State:
        txns = [t for src in sources for t in src.fetch()]
        log.info("PERCEIVE  read %d transactions from %d source(s)", len(txns), len(sources))
        return {"txns": txns}

    def detect_recurring(state: State) -> State:
        subs = detect_subscriptions(state["txns"], state["today"])
        log.info("DETECT    %d recurring subscriptions found among %d distinct merchants",
                 len(subs), len({t.merchant.lower() for t in state["txns"]}))
        return {"subs": subs}

    def update_registry(state: State) -> State:
        usage = CsvUsageProvider(usage_path)
        for s in state["subs"]:
            registry.upsert(s)
            registry.set_last_used(s.merchant, usage.last_used(s.merchant))
        log.info("REGISTRY  upserted %d subscriptions into %s", len(state["subs"]), db_path.name)
        return {}

    def signals_and_gate(state: State) -> State:
        candidates, skipped = [], []
        for sub in registry.get_all():
            sig = compute_signals(sub, state["today"])
            reasons = candidate_reasons(sub, sig)
            if reasons:
                candidates.append(Candidate(sub=sub, signals=sig, reasons=reasons,
                                            obvious=obvious_reasons(sig)))
            else:
                skipped.append(sub.merchant)
                registry.clear_evaluation(sub.merchant)  # issue gone: forget it
                registry.clear_decision(sub.merchant)
        log.info("GATE      %d candidates for review, %d skipped", len(candidates), len(skipped))
        for m in skipped:
            log.info("  SKIP %-18s no signal crossed the candidate gate (no LLM call)", m)
        return {"candidates": candidates, "skipped": skipped}

    def route(state: State) -> str:
        return "reason" if state["candidates"] else "act"

    def reason(state: State) -> State:
        decisions, unchanged, fps = [], [], {}
        for c in state["candidates"]:
            m = c.sub.merchant
            fps[m] = fingerprint(c, state["today"])
            prior = registry.get_evaluation(m)
            settled = (prior and prior.fingerprint == fps[m] and prior.source == "llm"
                       and (not prior.alerted or prior.notified))
            if settled:
                log.info("REASON    %s: unchanged since last LLM review [%s], skipping LLM", m, fps[m])
                unchanged.append(m)
                continue
            log.info("REASON    %s: flagged for %s; strict flags: %s; issue [%s]", m,
                     c.reasons, c.obvious or "none", fps[m])
            decisions.append(reasoner.decide(c))
        return {"decisions": decisions, "unchanged": unchanged, "fingerprints": fps}

    def act(state: State) -> State:
        by_name = {c.sub.merchant: c for c in state.get("candidates", [])}
        for d in state.get("decisions", []):
            c, fp = by_name[d.merchant], state["fingerprints"][d.merchant]
            sig = c.signals
            facts = (f"price {sig.price_change_pct:+g}%, idle {sig.days_since_last_use}d, "
                     f"₹{sig.yearly_cost_inr:g}/yr, renews in {sig.days_to_renewal}d")
            prior = registry.get_evaluation(d.merchant)
            notified = bool(prior and prior.fingerprint == fp and prior.notified)
            if not d.alert:
                log.info("ACT       NO ACTION %s: %s  (%s; via %s)", d.merchant, d.reason, facts, d.source)
            elif notified:
                log.info("ACT       SUPPRESSED duplicate alert for %s: already notified for this issue [%s]",
                         d.merchant, fp)
            else:
                log.warning("ACT       ALERT [%s] %s: %s  (%s; via %s)", d.severity.upper(),
                            d.merchant, d.reason, facts, d.source)
                try:
                    if notifier:
                        notifier.send(d, sig)
                    notified = True
                except Exception:
                    log.exception("  Notification failed for %s; will retry next run", d.merchant)
            registry.save_decision(d)
            registry.save_evaluation(Evaluation(merchant=d.merchant, fingerprint=fp, source=d.source,
                                                alerted=d.alert, notified=notified))
        if not state.get("decisions") and not state.get("unchanged"):
            log.info("ACT       nothing to review: all subscriptions look fine")
        return {}

    g = StateGraph(State)
    for name, fn in [("perceive", perceive), ("detect_recurring", detect_recurring),
                     ("update_registry", update_registry), ("signals_and_gate", signals_and_gate),
                     ("reason", reason), ("act", act)]:
        g.add_node(name, fn)
    g.add_edge(START, "perceive")
    g.add_edge("perceive", "detect_recurring")
    g.add_edge("detect_recurring", "update_registry")
    g.add_edge("update_registry", "signals_and_gate")
    g.add_conditional_edges("signals_and_gate", route, {"reason": "reason", "act": "act"})
    g.add_edge("reason", "act")
    g.add_edge("act", END)
    return g.compile()
