import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from agent import config, ui


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "ALERT_LOG", tmp_path / "alerts.jsonl")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(url, body, headers=None):
    req = urllib.request.Request(url, json.dumps(body).encode(), headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_is_served(server):
    with urllib.request.urlopen(server + "/") as r:
        assert b"Subscription Watchdog" in r.read()


def test_post_without_custom_header_is_forbidden(server):
    assert post(server + "/api/run", {})[0] == 403
    assert post(server + "/api/reset", {})[0] == 403


def test_run_then_state_shows_subscriptions_and_decisions(server):
    code, out = post(server + "/api/run", {"use_llm": False}, {"X-Requested-With": "watchdog"})
    assert code == 200 and any("GATE" in line for line in out["log"])
    with urllib.request.urlopen(server + "/api/state") as r:
        state = json.loads(r.read())
    by = {s["merchant"]: s for s in state["subscriptions"]}
    assert len(by) == 7
    assert by["StreamFlix"]["decision"]["alert"] is True and by["StreamFlix"]["notified"] is True
    assert by["Spotifree"]["decision"] is None and by["Spotifree"]["gate"] == []
    assert state["totals"]["alerts_sent"] == 3
    assert state["alerts"][0]["sent_at"]


def test_bad_date_rejected(server):
    code, out = post(server + "/api/run", {"today": "nope"}, {"X-Requested-With": "watchdog"})
    assert code == 400


def test_reset_clears_state(server):
    h = {"X-Requested-With": "watchdog"}
    post(server + "/api/run", {"use_llm": False}, h)
    assert post(server + "/api/reset", {}, h)[0] == 200
    with urllib.request.urlopen(server + "/api/state") as r:
        assert json.loads(r.read())["subscriptions"] == []
