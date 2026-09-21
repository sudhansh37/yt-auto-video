"""
Hindi Shorts Bot - main pipeline
================================
1. Source channel se ek short pick karo (latest / random / mix) - duplicates skip
2. yt-dlp se download karo (full HD vertical)
3. Gemini se video analysis -> Hindi script + title + description
4. TTS se Hindi voice banao (Gemini TTS; fail hone pe fallback chain)
5. ffmpeg: 9:16 crop (top safe, bottom se crop) + white canvas + zoom/pan
   + color grade + sharpness + slow background music + TIME-SYNCED
   DEVENAGARI CAPTIONS (model ke script se - jo voice bolti hai wahi dikhta hai)
6. YouTube Data API se Short upload karo ("AI use" disclosure ke saath)
7. history.json me video id save karo (isliye kabhi duplicate nahi)
8. publish_log.json me time save karo (watchdog isse missed-slot check karta hai)

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
from uploader import upload_video             # noqa: E402


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def log_publish(yt_id):
    """publish_log.json me aaj ki entry likho (watchdog ke liye).

    watchdog har 30 min me isse padh kar check karta hai ki aaj ke
    slots (10:00 / 15:00 IST) pe video publish hui ya nahi.
    """
    log_path = ROOT / "data" / "publish_log.json"
    log_path.parent.mkdir(exist_ok=True)
    try:
        log = json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - file na ho / kharab ho
        log = {"publishes": []}
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    log["publishes"].append({
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "yt": yt_id,
    })
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1),
                        encoding="utf-8")


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

    # ---- 5. edit: crop + effects + music + CAPTIONS ----
    variant = random.choice(cfg["effects"]["variants"])
    out = edit_video(src, audio, work / "final.mp4", variant, cfg["effects"],
                     script=analysis["script"])
    print(f"Edit complete (effect={variant}, captions=on) -> {out.name}")

    # ---- 6. YouTube upload ("AI use" disclosure auto-on) ----
    title = analysis["title"]
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"
    yt_id = upload_video(
        out, title, analysis["description"],
        cfg["youtube"].get("tags", []), cfg["youtube"],
    )
    print("YouTube pe upload ho gaya.")

    # ---- 7. history + publish log save ----
    mark_used(history, video_id)
    trim_history(history, cfg["channel"]["max_history"])
    save_history(history)
    log_publish(yt_id)
    print("History + publish log update ho gayi - ye video dobara use nahi hogi.")


if __name__ == "__main__":
    main()
