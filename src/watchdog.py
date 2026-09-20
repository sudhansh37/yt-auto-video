"""
Watchdog - "missed slot" catch-up system
========================================
GitHub Actions ke cron schedules kabhi-kabhi high load me DROP ho jaate
hain (kabhi 50+ min late bhi). Ye watchdog us problem ka ilaaj hai:

  - Har 30 minute (watchdog.yml ka cron) ye script check karti hai:
    aaj (IST) ke kaunse publish slots ka time nikal chuka hai?
      SLOTS = 08:15, 11:00, 13:45, 16:30 IST
    aur unme se kitne cover ho chuke hain?
  - Jo slot GRACE_MIN (45 min) tak me bhi cover nahi hua - matlab
    scheduled run drop hua tha - watchdog wahi pipeline chala kar video
    publish kar deta hai.
  - Ek hi chalu watchdog run SAARE missing slots ek-ek karke catch-up
    kar leta hai, isliye din me kahin bhi ek bhi watchdog run bach
    jaye to bhi videos publish ho hi jaati hain.

DOUBLE CHECK (over-publish se bachav):
  - Primary check: data/publish_log.json (time-window ke saath precise)
  - Secondary check: data/history.json me aaj kitni videos mark hui
    (har run - daily bhi - ise commit karta hai, isliye ye hamesha
    fresh rehta hai). Jo bhi count ZYADA dikhaye, wahi maana jata hai
    (missing = dono me se minimum) - isliye kabhi duplicate publish
    nahi hota chahe log commit fail bhi ho jaye.

Usage:
    python src/watchdog.py               # check + zaroorat ho to publish
    python src/watchdog.py --check-only  # sirf check (exit 0 = miss hua,
                                          # exit 3 = sab theek)
"""
import json
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
IST = ZoneInfo("Asia/Kolkata")

# din ke publish slots (IST) - daily.yml ke crons se match karte hain
SLOTS = ["08:15", "11:00", "13:45", "16:30"]
GRACE_MIN = 45   # slot ke itne min baad tak video aani chahiye
EARLY_MIN = 5    # slot se thoda pehle wali video bhi cover maan li jaati hai


def _load_log():
    """data/publish_log.json se aaj ki publishes padho (corrupt ho to khali)."""
    path = ROOT / "data" / "publish_log.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("publishes", [])
    except Exception:  # noqa: BLE001 - file na ho / kharab ho
        return []


def _count_today_history(now):
    """history.json me AAJ kitni videos mark hue hain?

    Ye doosra (zyada reliable) source of truth hai - daily.yml ke scheduled
    runs publish_log.json commit nahi kar sakte the purane bug me, lekin
    history.json HAR run commit karta hai. Isliye agar koi video publish
    ho chuki hai aur log me na dikhe, to yahan se count pakka milega.

    NOTE: history UTC date likhti hai (runner UTC pe hota hai), isliye
    IST ki aaj + UTC ki aaj - dono dates count karte hain.
    """
    path = ROOT / "data" / "history.json"
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    utc_today = datetime.now(ZoneInfo("UTC")).date().isoformat()
    ist_today = now.date().isoformat()
    return sum(1 for v in history.values() if v in (ist_today, utc_today))


def check(now=None):
    """Kitne slots miss hue hain? (int) - negative kabhi nahi hota."""
    now = now or datetime.now(IST)
    today = now.date()

    # aaj ki publishes (time ke hisaab se sorted)
    pubs = sorted(
        datetime.combine(today, time.fromisoformat(e["time"]), IST)
        for e in _load_log()
        if e.get("date") == today.isoformat() and e.get("time")
    )

    # kaunse slots ka time (+ grace) nikal chuka hai
    due = [
        datetime.combine(today, time.fromisoformat(s), IST)
        for s in SLOTS
        if now >= datetime.combine(today, time.fromisoformat(s), IST)
        + timedelta(minutes=GRACE_MIN)
    ]

    # greedy assignment: k-i publish, k-i due slot ko cover karti hai
    # (publish us slot se >= EARLY_MIN pehle tak hi count hoti hai)
    pi = covered_log = 0
    for slot in due:
        while pi < len(pubs) and pubs[pi] < slot - timedelta(minutes=EARLY_MIN):
            pi += 1  # ye publish is slot se bahut purani hai - skip
        if pi < len(pubs):
            covered_log += 1
            pi += 1

    # secondary source: history.json me aaj ki count (har run commit karta hai)
    covered_hist = _count_today_history(now)

    # dono me se jo zyada bataye, wahi final (duplicate se bachav)
    covered = max(covered_log, covered_hist)
    missing = max(0, len(due) - covered)

    print(
        f"[watchdog] {now:%d %b %I:%M %p} IST | slots due: {len(due)} | "
        f"log se covered: {covered_log} | history se aaj: {covered_hist} | "
        f"final missing: **{missing}**"
    )
    return missing


def publish_catchup(missing):
    """Missing slots ke liye ek-ek karke pipeline chalao."""
    for i in range(missing):
        print(f"[watchdog] catch-up publish {i + 1}/{missing} chala raha hoon...")
        subprocess.run(
            [sys.executable, str(ROOT / "src" / "main.py"), "mix"],
            check=True,
        )


def main(check_only=False):
    missing = check()
    if missing <= 0:
        print("[watchdog] sab slots covered hain - kuch karne ki zaroorat nahi.")
        sys.exit(3 if check_only else 0)
    if check_only:
        print(f"[watchdog] {missing} slot miss hua hai - publish ki zaroorat hai.")
        sys.exit(0)
    publish_catchup(missing)
    print("[watchdog] catch-up complete.")
    sys.exit(0)


if __name__ == "__main__":
    main(check_only="--check-only" in sys.argv)
