"""Single place for every tunable value: thresholds, paths, env vars.

Two-level thresholds explain why the agent is not just a rule engine:
  * CANDIDATE thresholds are deliberately loose. Crossing one only means
    "worth looking at" and sends the subscription to the LLM.
  * OBVIOUS thresholds are strict. They are hints passed to the LLM as
    context (and used by the offline fallback when the LLM is unavailable).
The LLM weighs combinations (e.g. small hike + long inactivity) that no
single rule captures.
"""
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"

TRANSACTIONS_CSV = DATA_DIR / "transactions.csv"
USAGE_CSV = DATA_DIR / "usage.csv"
DB_PATH = DATA_DIR / "registry.db"

# --- Reproducible "today" (override with --today YYYY-MM-DD) -------------
REFERENCE_DATE = date(2026, 10, 29)

# --- Mock FX rate: NOT live data, used only for the demo -----------------
MOCK_FX_TO_INR = {"INR": 1.0, "USD": 85.0}

# --- Candidate gate (loose): "worth the LLM's attention" -----------------
CANDIDATE_PRICE_HIKE_PCT = 5.0
CANDIDATE_INACTIVE_DAYS = 60
RENEWAL_WINDOW_DAYS = 21  # yearly renewal this close is a candidate

# --- Obvious-alert level (strict): context hints / offline fallback ------
OBVIOUS_PRICE_HIKE_PCT = 25.0
OBVIOUS_INACTIVE_DAYS = 120

# --- LLM ------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
LLM_MAX_RETRIES = 3

# --- Loop -----------------------------------------------------------------
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

# --- Category lookup (mock; a real system would classify merchants) -------
MERCHANT_CATEGORIES = {
    "streamflix": "entertainment",
    "streamplus": "entertainment",
    "primeshow annual": "entertainment",
    "spotifree": "music",
    "fitzone gym": "fitness",
    "clouddrive": "cloud storage",
    "codeassist": "developer tools",
}

# --- Extra paths ----------------------------------------------------------
INBOX_DIR = DATA_DIR / "inbox"
ALERT_LOG = LOG_DIR / "alerts.jsonl"
