"""
Hindi Shorts Bot - main pipeline
================================
1. HARD DAILY CAP: aaj ki 2 videos (YouTube API se verified) ho gayi to STOP
2. Source channel se ek short pick karo (latest / random / mix) - duplicates skip
3. yt-dlp se download karo (full HD vertical)
4. Gemini se video analysis -> Hindi script + Hinglish captions + title + description
5. TTS se Hindi voice banao (Gemini TTS; fail hone pe fallback chain)
6. ffmpeg: 9:16 crop (top safe, bottom se crop) + white canvas + zoom/pan
   + color grade + sharpness + slow background music + TIME-SYNCED
   HINGLISH CAPTIONS (model ke script se - jo voice bolti hai wahi dikhta hai)
7. upload se PEHLE history + 'pending' log entry (run kill ho to bhi safe)
8. YouTube Data API se Short upload karo ("AI use" disclosure ke saath)
9. pending entry ko real video id se confirm karo

Usage:
    python src/main.py           # default mode = mix
    python src/main.py latest    # sabse nayi unused video
    python src/main.py random    # koi bhi unused purani video
    python src/main.py mix       # 50% latest, 50% random purani
"""
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from analyzer import analyze_video            # noqa: E402
from downloader import download_video, list_short_ids  # noqa: E402
from editor import edit_video, get_duration   # noqa: E402
from history import load_history, mark_used, save_history, trim_history  # noqa: E402
from tts import synthesize                    # noqa: E402
from uploader import upload_video, count_today_uploads  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
MAX_PER_DAY = 2   # din me SIRF itni videos (10:00 / 15:00 slots)


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_publish_log():
    log_path = ROOT / "data" / "publish_log.json"
    try:
        return json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - file na ho / kharab ho
        return {"publishes": []}


def log_publish(yt_id):
    """publish_log.json me aaj ki entry likho (watchdog ke liye).

    watchdog har 30 min me isse padh kar check karta hai ki aaj ke
    slots (10:00 / 15:00 IST) pe video publish hui ya nahi.
    """
    log_path = ROOT / "data" / "publish_log.json"
    log_path.parent.mkdir(exist_ok=True)
    log = _load_publish_log()
    now = datetime.now(IST)
    log["publishes"].append({
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "yt": yt_id,
    })
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1),
                        encoding="utf-8")


def confirm_pending_publish(yt_id):
    """Upload se pehle likhi gayi 'pending' entry ko real video id se badlo.

    Agar 'pending' entry nahi mili (kisi wajah se), to fresh entry likh do.
    """
    log_path = ROOT / "data" / "publish_log.json"
    log = _load_publish_log()
    pubs = log.get("publishes", [])
    today = datetime.now(IST).date().isoformat()
    for e in reversed(pubs):
        if e.get("yt") == "pending" and e.get("date") == today:
            e["yt"] = yt_id
            log_path.write_text(
                json.dumps(log, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            return
    log_publish(yt_id)   # pending nahi mili - nayi entry


def _local_count_today():
    """publish_log.json me aaj (IST) ki kitni entries hain? (pending included)"""
    today = datetime.now(IST).date().isoformat()
    return sum(
        1 for e in _load_publish_log().get("publishes", [])
        if e.get("date") == today
    )


def daily_cap_reached():
    """AAJ ki limit (2 videos) already ho gayi? (True/False)

    DO sources se check hota hai:
      1. local publish_log.json (fast)
      2. YouTube API se aaj ki uploaded videos ka REAL count
         (authoritative - git commit fail ho jaye tab bhi sahi rahega)
    """
    local = _local_count_today()
    if local >= MAX_PER_DAY:
        print(f"[cap] publish_log me aaj ki {local} videos already hain - "
              f"limit {MAX_PER_DAY} - aaj aur upload NAHI hoga.")
        return True
    try:
        yt_count = count_today_uploads()
        print(f"[cap] YouTube ke hisaab se aaj {yt_count} videos upload ho chuki hain.")
        if yt_count >= MAX_PER_DAY:
            print(f"[cap] YouTube limit {MAX_PER_DAY} poori - aaj bas, kal 10 AM pe fir se.")
            return True
    except Exception as e:  # noqa: BLE001 - API fail ho to local pe chalte hain
        print(f"[cap] WARNING: YouTube count nahi ho paya ({e}) - "
              f"local log ({local}/{MAX_PER_DAY}) ka use kar raha hoon.")
    return False


def pick_video(ids, history, mode):
    """Unused videos me se ek choose karo."""
    fresh = [i for i in ids if i not in history]
    if not fresh:
        return None
    if mode == "latest":
        return fresh[0]            # playlist order = newest first
    if mode == "random":
        return random.choice(fresh)
    # mix: aadhi baar latest, aadhi baar koi purani
    if random.random() < 0.5 or len(fresh) == 1:
        return fresh[0]
    return random.choice(fresh[1:])


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "mix"
    cfg = load_config()

    # ---- 0. HARD DAILY CAP ----
    # Din me SIRF 2 videos (10 AM + 3 PM). Chahe cron double-fire ho,
    # watchdog over-retry kare, ya purani run ka data commit na hua ho -
    # YouTube se seedha count karke aaj ki limit cross NAHI hogi.
    if daily_cap_reached():
        sys.exit(0)   # exit 0: watchdog ise 'done' maane, dobara na chalaye

    work = ROOT / "work"
    work.mkdir(exist_ok=True)

    # ---- 1. video select (duplicate check ke saath) ----
    history = load_history()
    ids = list_short_ids(cfg["channel"]["url"])
    if not ids:
        raise RuntimeError("Channel ki shorts list nahi mili - config.yaml me 'channel.url' check karo.")

    fresh_count = sum(1 for i in ids if i not in history)
    print(f"Channel pe {len(ids)} shorts mili, {fresh_count} abhi tak use nahi hui.")

    if fresh_count == 0:
        print("Sab videos use ho chuki - history reset kar raha hoon (purani videos dobara aa sakti hain).")
        history = {}

    video_id = pick_video(ids, history, mode)
    print(f"Selected video: https://youtu.be/{video_id}  (mode={mode})")

    # ---- 2. download ----
    src = download_video(video_id, work)
    duration = get_duration(src)
    print(f"Download complete ({duration:.0f}s).")

    # ---- 3. Gemini analysis ----
    print("Gemini se analysis ho rahi hai...")
    analysis = analyze_video(src, duration, cfg["gemini"])
    print(f"  Title: {analysis['title']}")

    # ---- 4. TTS (Gemini TTS Hindi voice, no-gap cleanup ke saath) ----
    print("Hindi voice generate ho rahi hai (Gemini TTS)...")
    audio = synthesize(analysis["script"], cfg["tts"], work / "voice.mp3")
    print(f"Hindi voice ready: {audio.name}")

    # ---- 5. edit: crop + effects + music + HINGLISH CAPTIONS ----
    variant = random.choice(cfg["effects"]["variants"])
    # on-screen captions: HINGLISH (roman) - Devanagari sirf voice ke liye
    cap_lang = (cfg.get("captions") or {}).get("language", "hinglish")
    if cap_lang == "devanagari" or not str(analysis.get("captions") or "").strip():
        caption_text = analysis["script"]
    else:
        caption_text = analysis["captions"]
    out = edit_video(src, audio, work / "final.mp4", variant, cfg["effects"],
                     script=caption_text)
    print(f"Edit complete (effect={variant}, captions={cap_lang}) -> {out.name}")

    # ---- 6. upload se PEHLE history + pending log (KILL-SAFE) ----
    # Agar run upload ke dauran/beech me kill ho jaye (timeout aadi), to bhi:
    #   - ye source video dobara use nahi hogi (history saved)
    #   - 'pending' entry watchdog ko batayegi ki slot cover ho gaya
    #     (duplicate upload ka poora chain yahin kat jata hai)
    mark_used(history, video_id)
    trim_history(history, cfg["channel"]["max_history"])
    save_history(history)
    log_publish("pending")

    # ---- 7. YouTube upload ("AI use" disclosure auto-on) ----
    title = analysis["title"]
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"
    yt_id = upload_video(
        out, title, analysis["description"],
        cfg["youtube"].get("tags", []), cfg["youtube"],
    )
    print("YouTube pe upload ho gaya.")

    # ---- 8. pending entry ko real video id se confirm karo ----
    confirm_pending_publish(yt_id)
    print("History + publish log update ho gayi - ye video dobara use nahi hogi.")


if __name__ == "__main__":
    main()
