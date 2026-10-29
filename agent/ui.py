"""Tiny local dashboard. Standard library only: no Flask, no frontend build.

  python -m agent.ui            then open http://127.0.0.1:8000

GET  /              the page (agent/web/index.html)
GET  /api/state     registry + signals + last decisions + recent alerts, as JSON
POST /api/run       run the agent once, return the decision log
POST /api/reset     delete the demo database and alert history

It only reads the same SQLite registry the agent writes, so the UI adds no
logic of its own: every number shown was computed by agent/signals.py.
"""
import argparse
import json
import logging
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent import config
from agent.graph import build_graph
from agent.log import log, setup_logging
from agent.notifier import FileNotifier
from agent.perceive import EmailPerceiver
from agent.reasoner import make_reasoner
from agent.registry import Registry
from agent.signals import candidate_reasons, compute_signals, obvious_reasons

PAGE = Path(__file__).parent / "web" / "index.html"
RUN_LOCK = threading.Lock()  # one agent run at a time


def recent_alerts(limit: int = 15) -> list[dict]:
    if not config.ALERT_LOG.exists():
        return []
    rows = [json.loads(line) for line in config.ALERT_LOG.read_text().splitlines() if line.strip()]
    return [{"sent_at": r.get("sent_at", ""), **r["decision"]} for r in reversed(rows[-limit:])]


def snapshot(today: date) -> dict:
    reg = Registry(config.DB_PATH)
    decisions = reg.get_decisions()
    subs = []
    for s in reg.get_all():
        sig = compute_signals(s, today)
        gate = candidate_reasons(s, sig)
        d, ev = decisions.get(s.merchant), reg.get_evaluation(s.merchant)
        subs.append({
            "merchant": s.merchant, "category": s.category, "cadence": s.cadence, "status": s.status,
            "price": s.current_price_inr, "history": [[str(dt), p] for dt, p in s.price_history],
            "price_change_pct": sig.price_change_pct, "days_since_use": sig.days_since_last_use,
            "yearly_cost": sig.yearly_cost_inr, "days_to_renewal": sig.days_to_renewal,
            "gate": gate, "obvious": obvious_reasons(sig),
            "decision": d.model_dump() if d else None,
            "notified": ev.notified if ev else None,
        })
    active = [x for x in subs if x["status"] == "active"]
    return {
        "today": str(today), "model": config.GROQ_MODEL, "llm_ready": bool(config.GROQ_API_KEY),
        "subscriptions": subs, "alerts": recent_alerts(),
        "totals": {"count": len(active), "yearly_cost": round(sum(x["yearly_cost"] for x in active), 2),
                   "alerts_sent": sum(1 for x in subs if x["notified"])},
    }


class _ListHandler(logging.Handler):
    def __init__(self, sink: list):
        super().__init__()
        self.sink = sink

    def emit(self, record):
        self.sink.append(record.getMessage())


def run_agent(use_llm: bool, today: date, inbox: bool) -> list[str]:
    lines: list[str] = []
    with RUN_LOCK:
        handler = _ListHandler(lines)
        log.addHandler(handler)
        if log.level == logging.NOTSET:  # logging not set up yet (e.g. in tests)
            log.setLevel(logging.INFO)
        try:
            extras = (EmailPerceiver(config.INBOX_DIR),) if inbox else ()
            graph = build_graph(make_reasoner(use_llm), FileNotifier(config.ALERT_LOG),
                                db_path=config.DB_PATH, extra_perceivers=extras)
            log.info("=== Watchdog run (dashboard), reference date %s ===", today)
            graph.invoke({"today": today})
        finally:
            log.removeHandler(handler)
    return lines


def reset_state() -> None:
    for p in (config.DB_PATH, config.ALERT_LOG):
        p.unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode())

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            try:
                params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                today = date.fromisoformat(params.get("today", str(config.REFERENCE_DATE)))
            except ValueError:
                return self._json({"error": "today must be YYYY-MM-DD"}, 400)
            self._json(snapshot(today))
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        # A custom header can't be sent cross-site without a CORS preflight,
        # which this server never grants: blocks other web pages from POSTing here.
        if self.headers.get("X-Requested-With") != "watchdog":
            return self._json({"error": "forbidden"}, 403)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad JSON"}, 400)
        if self.path == "/api/run":
            try:
                today = date.fromisoformat(body.get("today", str(config.REFERENCE_DATE)))
                lines = run_agent(bool(body.get("use_llm", True)), today, bool(body.get("inbox", False)))
            except ValueError:
                return self._json({"error": "today must be YYYY-MM-DD"}, 400)
            except Exception as e:  # show the problem in the UI instead of a blank page
                log.exception("Run failed")
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            self._json({"log": lines})
        elif self.path == "/api/reset":
            reset_state()
            self._json({"ok": True})
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *args):  # keep the terminal for the agent's own log
        pass


def main() -> None:
    p = argparse.ArgumentParser(description="Subscription Watchdog dashboard")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    setup_logging()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)  # localhost only
    print(f"Dashboard: http://127.0.0.1:{args.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
