"""
Watchdog - "missed slot" catch-up system
========================================
GitHub Actions ke cron schedules kabhi-kabhi high load me DROP ho jaate
hain (kabhi 50+ min late bhi). Ye watchdog us problem ka ilaaj hai:

  - Har 30 minute (watchdog.yml ka cron) ye script check karti hai:
    aaj (IST) ke kaunse publish slots ka time nikal chuka hai?
      SLOTS = 10:00, 15:00 IST  (din me 2 videos)
    aur unme se kitne cover ho chuke hain?
  - Jo slot GRACE_MIN (45 min) tak me bhi cover nahi hua - matlab
    scheduled run drop hua tha - watchdog wahi pipeline chala kar video
    publish kar deta hai.

MULTI-CHANNEL:
  - publish_log.json entries me 'ch' field hota hai (channel number,
    purani entries = 1). Har channel ke slots alag-alag check hote hain.
  - Sirf wahi channels monitor hote hain jinke secrets set hain
    (YT_CLIENT_ID_2 aadi) - channel 2 ke secrets na hone pe wo
    silently skip hota hai.

DOUBLE CHECK (over-publish se bachav):
  - Primary check: data/publish_log.json (per-channel, time-window precise)
    + upload se pehle likhi 'pending' entry (run kill ho to bhi covered)
  - FINAL GUARD: main.py khud YouTube API se check karta hai ki us channel
    pe aaj kitni videos upload ho chuki hain - limit (2) poori ho to upload
    se pehle hi ruk jata hai. Isliye over-publish ab impossible hai.
  - Ek watchdog run me HAR CHANNEL ke liye sirf 1 catch-up publish hota
    hai - data stale ho tab bhi maximum 1 video per channel extra upload
    ho sakti hai, aur uske baad main.py ka cap rok dega.

FAIL-SOFT (Gemini outage aadi):
  - catch-up publish fail ho (jaise Gemini 503) to watchdog CRASH nahi
    karta - sirf warning dekar nikal jaata hai, aur agli run (30 min
    baad) me dobara try karta hai. Din me jab bhi Gemini wapas aayega,
    videos automatic publish ho jayengi.

Usage:
    python src/watchdog.py               # check + zaroorat ho to publish
    python src/watchdog.py --check-only  # sirf check (exit 0 = miss hua,
                                          # exit 3 = sab theek)
"""
import json
import os
import subprocess
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
IST = ZoneInfo("Asia/Kolkata")

# din ke publish slots (IST) - daily.yml ke crons se match karte hain
SLOTS = ["10:00", "15:00"]
GRACE_MIN = 45   # slot ke itne min baad tak video aani chahiye
EARLY_MIN = 5    # slot se thoda pehle wali video bhi cover maan li jaati hai

# Ek watchdog run me SIRF itne catch-up publishes PER CHANNEL (safety):
# data stale ho to bhi ek hi run se 2-3 videos ek saath upload nahi hongi.
# Baaki missing slot agli run (30 min baad) me cover hoga - aur main.py ka
# YouTube-side daily cap (2/din/channel) waise bhi hard limit hai.
MAX_CATCHUP_PER_RUN = 1


def _load_log():
    """data/publish_log.json se aaj ki publishes padho (corrupt ho to khali)."""
    path = ROOT / "data" / "publish_log.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("publishes", [])
    except Exception:  # noqa: BLE001 - file na ho / kharab ho
        return []


def _count_today_history(now):
    """history.json me AAJ kitni videos mark hui hain? (sirf info ke liye)

    Ab ye sirf print ke liye hai - decision me use NAHI hota kyunki
    history SHARED hai (dono channels) aur per-channel attribution nahi
    hota. Decision publish_log ke 'ch' field se hota hai, aur asli
    guarantee main.py ka YouTube-side per-channel cap deta hai.
    """
    path = ROOT / "data" / "history.json"
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    utc_today = datetime.now(ZoneInfo("UTC")).date().isoformat()
    ist_today = now.date().isoformat()
    return sum(1 for v in history.values() if v in (ist_today, utc_today))


def active_channels():
    """Jinke secrets set hain wahi channels monitor karo (1 hamesha).

    stdlib-only env check (requests import NAHI - no-op watchdog runs
    pip install ke bina bhi chalte hain).
    """
    channels = [1]
    for ch in (2, 3, 4):
        suffix = "" if ch == 1 else f"_{ch}"
        if all(os.environ.get(n + suffix) for n in
               ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")):
            channels.append(ch)
    return channels


def check(now=None):
    """Har channel ke liye kitne slots miss hue? -> {channel: missing}.

    publish_log ki entries 'ch' field se channel batati hain (purani
    entries = channel 1). Har channel ke slots alag-alag count hote hain.
    """
    now = now or datetime.now(IST)
    today = now.date()

    # aaj ki publishes, channel ke hisaab se (time me sorted)
    pubs_by_ch = {}
    for e in _load_log():
        if e.get("date") == today.isoformat() and e.get("time"):
            ch = e.get("ch", 1)
            pubs_by_ch.setdefault(ch, []).append(
                datetime.combine(today, time.fromisoformat(e["time"]), IST)
            )

    # kaunse slots ka time (+ grace) nikal chuka hai (sab channels same)
    due = [
        datetime.combine(today, time.fromisoformat(s), IST)
        for s in SLOTS
        if now >= datetime.combine(today, time.fromisoformat(s), IST)
        + timedelta(minutes=GRACE_MIN)
    ]

    missing_by_ch = {}
    for ch in active_channels():
        pubs = sorted(pubs_by_ch.get(ch, []))
        # greedy assignment: k-i publish, k-i due slot ko cover karti hai
        # (publish us slot se >= EARLY_MIN pehle tak hi count hoti hai)
        pi = covered = 0
        for slot in due:
            while pi < len(pubs) and pubs[pi] < slot - timedelta(minutes=EARLY_MIN):
                pi += 1  # ye publish is slot se bahut purani hai - skip
            if pi < len(pubs):
                covered += 1
                pi += 1
        missing_by_ch[ch] = max(0, len(due) - covered)

    hist_today = _count_today_history(now)
    summary = " | ".join(
        f"ch{ch}: missing {m}" for ch, m in missing_by_ch.items()
    )
    print(
        f"[watchdog] {now:%d %b %I:%M %p} IST | slots due: {len(due)} | "
        f"history (total dono ch) aaj: {hist_today} | {summary}"
    )
    return missing_by_ch


def publish_catchup(missing_by_ch):
    """Har channel ke missing slots ke liye pipeline chalao (fail-soft).

    Ek run me HAR CHANNEL ke liye SIRF MAX_CATCHUP_PER_RUN (1) publish
    hota hai - baaki slots agli watchdog run (30 min baad) me. Har publish
    se pehle main.py khud YouTube-side daily cap (us channel ka) check
    karta hai, isliye over-upload ka raasta poori tarah band hai.

    Koi bhi run fail ho (jaise Gemini 503 outage) to crash nahi karte -
    agli watchdog run (30 min baad) dobara try karegi.
    """
    total_ok = 0
    for ch, missing in missing_by_ch.items():
        n = min(missing, MAX_CATCHUP_PER_RUN)
        if n <= 0:
            continue
        if n < missing:
            print(f"[watchdog] ch{ch}: {missing} slots missing the, par is run "
                  f"me sirf {n} publish karunga (safety) - baaki 30 min baad.")
        ok = 0
        for i in range(n):
            print(f"[watchdog] ch{ch}: catch-up publish {i + 1}/{n} chala raha hoon...")
            r = subprocess.run(
                [sys.executable, str(ROOT / "src" / "main.py"), "mix", str(ch)],
                check=False,   # fail-soft: crash nahi, agli run me retry
            )
            if r.returncode == 0:
                ok += 1
            else:
                print(f"[watchdog] ch{ch}: WARNING: publish {i + 1}/{n} fail hua "
                      f"(exit {r.returncode}) - agli watchdog run (30 min baad) "
                      f"me dobara try hoga.")
        total_ok += ok
    return total_ok


def main(check_only=False):
    missing_by_ch = check()
    total_missing = sum(missing_by_ch.values())
    if total_missing <= 0:
        print("[watchdog] sab channels ke slots covered hain - kuch karne ki zaroorat nahi.")
        sys.exit(3 if check_only else 0)
    if check_only:
        print(f"[watchdog] {total_missing} slot miss hue hain - publish ki zaroorat hai.")
        sys.exit(0)
    ok = publish_catchup(missing_by_ch)
    if ok < total_missing:
        print(f"[watchdog] {total_missing - ok} publish baaki hai - 30 min me retry hoga.")
    else:
        print("[watchdog] catch-up complete.")
    sys.exit(0)


if __name__ == "__main__":
    main(check_only="--check-only" in sys.argv)
