"""History tracking - jo videos already use ho chuki hain, wo dobara na aayein.

data/history.json me format:  {"video_id": "use hone ki date (YYYY-MM-DD)"}
GitHub Actions har run ke baad is file ko commit kar deta hai,
isliye har run ko pata hota hai kaunsi videos already use ho chuki hain.
"""
import json
from datetime import date
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "history.json"


def load_history():
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("Warning: history.json corrupt hai - fresh start kar rahe hain.")
    return {}


def save_history(history):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def mark_used(history, video_id):
    history[video_id] = date.today().isoformat()


def trim_history(history, max_entries):
    """Purani (sabse pehle use hui) entries hata do jab limit cross ho jaye."""
    while len(history) > max_entries:
        oldest = next(iter(history))   # dict insertion order == use ki date order
        del history[oldest]
