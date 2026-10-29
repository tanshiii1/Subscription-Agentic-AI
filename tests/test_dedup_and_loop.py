import json
import shutil
from datetime import date

from agent import config
from agent.graph import build_graph
from agent.loop import watch
from agent.notifier import FileNotifier, Notifier
from agent.perceive import EmailPerceiver
from agent.reasoner import Reasoner


class Recorder(Notifier):
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, decision, signals):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append(decision.merchant)


class CountingLLM:
    def __init__(self):
        self.calls = 0

    def __call__(self, system, user):
        self.calls += 1
        return json.dumps({"alert": True, "severity": "medium", "reason": "Worth a look."})


def run(graph, today=config.REFERENCE_DATE):
    return graph.invoke({"today": today})


def test_second_run_sends_nothing_and_skips_llm(tmp_path):
    llm, rec = CountingLLM(), Recorder()
    g = build_graph(Reasoner(llm), rec, db_path=tmp_path / "t.db")
    run(g)
    assert llm.calls == 5 and len(rec.sent) == 5
    run(g)
    assert llm.calls == 5 and len(rec.sent) == 5  # nothing new: no LLM, no alerts


def test_new_price_hike_alerts_again(tmp_path):
    txn = tmp_path / "tx.csv"
    shutil.copy(config.TRANSACTIONS_CSV, txn)
    llm, rec = CountingLLM(), Recorder()
    g = build_graph(Reasoner(llm), rec, db_path=tmp_path / "t.db", txn_path=txn)
    run(g)
    rec.sent.clear()
    with open(txn, "a") as f:
        f.write("2026-11-05,StreamFlix,999,INR\n")  # second hike: 799 -> 999
    run(g, date(2026, 11, 6))
    assert "StreamFlix" in rec.sent


def test_notifier_failure_is_retried_next_run(tmp_path):
    llm = CountingLLM()
    bad = Recorder(fail=True)
    run(build_graph(Reasoner(llm), bad, db_path=tmp_path / "t.db"))
    good = Recorder()
    run(build_graph(Reasoner(llm), good, db_path=tmp_path / "t.db"))
    assert len(good.sent) == 5  # none were marked notified, so all are retried


def test_fallback_alert_not_repeated_when_llm_returns(tmp_path):
    db = tmp_path / "t.db"
    rec = Recorder()
    run(build_graph(Reasoner(None), rec, db_path=db))          # outage run
    first = set(rec.sent)
    assert "StreamFlix" in first
    run(build_graph(Reasoner(CountingLLM()), rec, db_path=db))  # LLM back
    assert rec.sent.count("StreamFlix") == 1                    # no duplicate


def test_file_notifier_writes_jsonl(tmp_path):
    path = tmp_path / "logs" / "alerts.jsonl"
    run(build_graph(Reasoner(CountingLLM()), FileNotifier(path), db_path=tmp_path / "t.db"))
    lines = path.read_text().splitlines()
    assert len(lines) == 5 and json.loads(lines[0])["decision"]["alert"] is True


# --- email perceiver ---
def test_email_perceiver_reads_txt_and_eml_and_skips_bad(tmp_path):
    body = "Date: 2026-07-09\nSubject: r\n\nMerchant: NewsDaily\nAmount: INR 1,199.50\n"
    (tmp_path / "a.txt").write_text(body)
    (tmp_path / "b.eml").write_text("Date: Thu, 09 Jul 2026 10:00:00 +0530\n\nMerchant: X\nAmount: USD 10\n")
    (tmp_path / "bad.txt").write_text("Date: 2026-07-09\n\nno amount here\n")
    (tmp_path / "ignore.md").write_text("x")
    txns = EmailPerceiver(tmp_path).fetch()
    assert [(t.merchant, t.amount, t.currency) for t in txns] == [
        ("NewsDaily", 1199.5, "INR"), ("X", 10.0, "USD")]
    assert txns[1].date == date(2026, 7, 9)


def test_inbox_service_becomes_subscription(tmp_path):
    g = build_graph(Reasoner(None), None, db_path=tmp_path / "t.db",
                    extra_perceivers=(EmailPerceiver(config.INBOX_DIR),))
    state = run(g)
    assert "NewsDaily" in {s.merchant for s in state["subs"]}
    assert "NewsDaily" in state["skipped"]  # no usage data -> not a candidate


# --- polling loop ---
def test_watch_reruns_only_when_inputs_change(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("a")
    runs, ticks = [], []

    def sleep(_):
        ticks.append(1)
        if len(ticks) == 2:       # between tick 2 and 3, the file changes
            f.write_text("bb")

    watch(runs.append, lambda: [f], lambda: date(2026, 1, 1), 1, sleep=sleep, max_ticks=4)
    assert len(runs) == 2          # tick 1 (first) and tick 3 (changed); ticks 2 and 4 skipped


def test_watch_survives_a_failing_run_and_retries(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("a")
    attempts = []

    def flaky(today):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("boom")

    watch(flaky, lambda: [f], lambda: date(2026, 1, 1), 1, sleep=lambda s: None, max_ticks=3)
    assert len(attempts) == 2      # failed once, retried, then settled
