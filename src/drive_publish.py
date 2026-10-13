"""
Drive -> YouTube publisher (MANUAL)
===================================
Ye script APNI videos (jo ek PUBLIC Google Drive folder me rakhi hain)
uthaata hai, existing pipeline (analyzer -> Hindi script, tts -> Hindi
Gemini voice, editor -> wahi look) se process karta hai, aur YouTube
(channel 1) pe upload karta hai.

Source YouTube channel ki jagah Google Drive folder hai - baaki poora
flow main.py jaisa hi hai. Helpers (load_config, log_publish,
confirm_pending_publish, revert_pending_publish) main.py se hi import
hote hain - koi copy-paste nahi.

DUPLICATE PROTECTION
--------------------
data/drive_done.json me processed videos ka record rakha jaata hai
(file id ke saath title + uska normalized 'norm' bhi). Do tarah se
dedupe hota hai:
  1) file id - jo id pehle se list me hai, wo skip ho jaati hai.
  2) title  - agar us video ka norm-title pehle kisi entry me hai to
     skip (chahe file id nayi ho - same title dobara upload nahi hoti).
Entry upload SUCCESS hone ke BAAD hi add hoti hai - agar run fail/bech
me kill ho jaye to add nahi hoti aur agli run me dobara try hota hai.
Purani entries (jisme sirf id/name thi) backwards-compatible hain - unka
norm name se derive ho jaata hai.

DRIVE ACCESS (koi API key NAHI)
-------------------------------
Drive folder PUBLIC hona chahiye. gdown public folder ko anonymously
list + download karta hai (koi service-account / OAuth / cookie nahi).
Folder private ho to gdown error deta hai - us case me script saaf
message ke saath ruk jaati hai (folder ko "Anyone with the link"
kar dijiye).

Folder badalna ho to env DRIVE_FOLDER_URL set kar do (ya neeche
DEFAULT_DRIVE_FOLDER_URL badal do).

Usage:
    python src/drive_publish.py [channel]
    python src/drive_publish.py        # channel 1 (default)
    python src/drive_publish.py 1      # channel 1
"""
import json
import os
import random
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from analyzer import analyze_video          # noqa: E402
from editor import edit_video, get_duration  # noqa: E402
from tts import synthesize                   # noqa: E402
from uploader import (                          # noqa: E402
    AuthError,
    channel_configured,
    post_comment,
    upload_video,
)
# main.py ke helpers reuse karo (copy-paste kam se kam)
from main import (                            # noqa: E402
    confirm_pending_publish,
    load_config,
    log_publish,
    revert_pending_publish,
)

# Public Drive folder (default). Env se override ho sakta hai.
DEFAULT_DRIVE_FOLDER_URL = (
    "https://drive.google.com/drive/folders/1AHUZf10cVA8Wur7Nbba47IKEXC7ecW9w"
)
DRIVE_FOLDER_URL = os.environ.get("DRIVE_FOLDER_URL", DEFAULT_DRIVE_FOLDER_URL).strip()

# Ek run me max kitni videos process/upload karni hain (default 1).
# Isse ek hi run me sirf ek video upload hoti hai - baaki agli run me.
# env MAX_PER_RUN se override ho sakta hai (default 1).
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "1"))

# Processed Drive file ids yahan track hote hain (git commit hota hai).
DONE_PATH = ROOT / "data" / "drive_done.json"

# Kaunsi extensions ko video maan kar process karna hai.
VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
    ".flv", ".mpeg", ".mpg", ".wmv", ".3gp", ".ts", ".mts", ".ogv",
}


# ---------------------------------------------------------------------------
# drive_done.json - processed Drive file ids
# ---------------------------------------------------------------------------

def load_done():
    """Pehle se process ho chuki Drive file ids ({id: {...}}) lauta do."""
    try:
        data = json.loads(DONE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001 - file na ho / kharab ho
        pass
    return {}


def save_done(done):
    """drive_done.json likho (data/ dir banake)."""
    DONE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DONE_PATH.write_text(
        json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def normalize_title(title):
    """Title ko normalize karo: lowercase + non-alphanumeric hata do.

    Emoji / non-ASCII strip ho jaate hain aur whitespace ek single space me
    collapse ho jaata hai - isliye same title (chahe emoji/spacing thoda
    alag ho) ek hi norm string deta hai.
    """
    return re.sub(r"[^a-z0-9]+", " ", str(title).lower()).strip()


def _entry_norm(rec):
    """Ek drive_done entry se uski norm-title nikalo (purani entries ke liye bhi)."""
    if not isinstance(rec, dict):
        return ""
    norm = rec.get("norm")
    if norm:
        return str(norm)
    # purani entry (sirf name/date/yt) - 'title' ho to seedha, warna 'name'
    # (jo full filename tha, extension ke saath) ka stem le kar norm banao.
    title = rec.get("title")
    if title:
        return normalize_title(title)
    name = rec.get("name")
    return normalize_title(Path(str(name)).stem) if name else ""


def title_already_done(done, title):
    """Ye title (norm) pehle kisi bhi entry me hai? -> True (dedupe)."""
    norm = normalize_title(title)
    if not norm:
        return False
    return any(_entry_norm(rec) == norm for rec in done.values())


def mark_done(done, file_id, name, yt_id):
    """Ek Drive file ko processed mark karo (upload ke baad).

    Record me title + norm bhi rakhte hain taaki same title (nayi file id
    ke saath dobara daali gayi ho) agli run me skip ho jaye. Purane fields
    (name/date) backwards-compat ke liye bane rehte hain.
    """
    title = Path(name).stem
    done[file_id] = {
        "id": file_id,
        "title": title,
        "norm": normalize_title(title),
        "yt": yt_id,
        "name": name,
        "date": date.today().isoformat(),
    }
    save_done(done)


# ---------------------------------------------------------------------------
# Google Drive (public folder) - list + download via gdown (no API key)
# ---------------------------------------------------------------------------

def list_drive_videos():
    """Public Drive folder ki video files list karo.

    Returns list of objects with .id, .path (naam), .local_path.
    Folder khali ho to [].
    """
    import gdown

    try:
        entries = gdown.download_folder(
            url=DRIVE_FOLDER_URL,
            output=str(ROOT / "work" / "_drive_list"),
            quiet=True,
            use_cookies=False,
            skip_download=True,
        )
    except Exception as e:  # noqa: BLE001 - private/nonexistent folder etc.
        raise RuntimeError(
            f"Drive folder list nahi ho paya ({e}). Sabse common wajah: folder "
            f"PUBLIC nahi hai - Drive me folder -> Share -> 'Anyone with the "
            f"link' -> Viewer kar dijiye. (URL: {DRIVE_FOLDER_URL})"
        ) from e

    videos = []
    for e in entries:
        name = Path(e.path).name
        if Path(name).suffix.lower() in VIDEO_EXTS:
            videos.append(e)
    return videos


def _ascii_safe_name(name):
    """Filename ko ASCII-safe banao (emoji/non-ASCII hata do).

    Gemini Files API upload ke waqt filename HTTP header me jaata hai - usme
    emoji/non-ASCII hone par "'ascii' codec can't encode character" crash hota
    hai. Sirf [A-Za-z0-9._-] rakho, baaki sab '_' kar do (suffix preserve).
    """
    stem = Path(name).stem
    suffix = Path(name).suffix
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "video"
    safe_suffix = re.sub(r"[^A-Za-z0-9.]+", "", suffix) or ".mp4"
    return safe_stem + safe_suffix


def download_drive_file(entry, work):
    """Ek Drive file ko locally download karo (gdown, no API key).

    File ko ASCII-safe naam pe rakhte hain - original naam me emoji ho sakta
    hai, aur wahi naam Gemini Files API upload pe ascii codec error deta hai.
    Original naam sirf logging ke liye use hota hai (process_one me).
    """
    import gdown

    dest = work / _ascii_safe_name(Path(entry.path).name)
    gdown.download(id=entry.id, output=str(dest), quiet=True, use_cookies=False)
    if not dest.exists() or dest.stat().st_size < 10_000:
        raise RuntimeError(
            f"Drive file download adhoora/khali aaya: {entry.path} (id={entry.id})"
        )
    return dest


# ---------------------------------------------------------------------------
# Upload ke baad related auto-comment
# ---------------------------------------------------------------------------

def _build_comment(analysis):
    """Analysis se ek related Hinglish auto-comment banao."""
    title = str(analysis.get("title") or "").strip()
    desc_lines = str(analysis.get("description") or "").strip().splitlines()
    first_line = desc_lines[0].strip() if desc_lines else ""
    text = (
        f"{title} 😲\n"
        f"Aapko ye pasand aaya? Aise hi amazing facts ke liye SUBSCRIBE karo! 🔔\n"
        f"Aapka kya lagta hai - comment me batao 👇"
    )
    if first_line:
        text = f"{text}\n{first_line}"
    return text


# ---------------------------------------------------------------------------
# Ek video ko process + upload karo
# ---------------------------------------------------------------------------

def process_one(entry, cfg, channel, work, done):
    """Ek Drive video: download -> analyze -> TTS -> edit -> upload.

    Success par yt_id lauta do aur drive_done.json update kar do.
    Fail par exception raise karo (drive_done me add NAHI hota).
    """
    name = Path(entry.path).name
    print(f"\n=== [{name}] (drive id={entry.id}) process shuru ===")

    src = download_drive_file(entry, work)
    duration = get_duration(src)
    print(f"  Download complete ({duration:.0f}s): {src.name}")

    # ---- Gemini analysis: Hindi script + title/desc/hashtags/tags ----
    print("  Gemini se analysis ho rahi hai...")
    analysis = analyze_video(src, duration, cfg["gemini"])
    print(f"  Title: {analysis['title']}")

    # ---- Hindi TTS (Gemini TTS; fail hone pe fallback chain) ----
    print("  Hindi voice generate ho rahi hai (Gemini TTS)...")
    audio = synthesize(analysis["script"], cfg["tts"], work / "voice.mp3")
    print(f"  Hindi voice ready: {audio.name}")

    # ---- edit: crop + effects + captions/top-text (main.py jaisa look) ----
    variant = random.choice(cfg["effects"]["variants"])
    cap_cfg = cfg.get("captions") or {}
    caption_text = None
    if cap_cfg.get("enabled", True):
        cap_lang = cap_cfg.get("language", "hinglish")
        if cap_lang == "devanagari" or not str(analysis.get("captions") or "").strip():
            caption_text = analysis["script"]
        else:
            caption_text = analysis["captions"]
    ov_cfg = cfg.get("overlay") or {}
    top_text = None
    if ov_cfg.get("enabled", True):
        top_text = str(analysis.get("top_text") or "").strip() or None

    out = edit_video(
        src, audio, work / "final.mp4", variant, cfg["effects"],
        script=caption_text, top_text=top_text,
    )
    print(f"  Edit complete (effect={variant}) -> {out.name}")

    # ---- upload se PEHLE 'pending' log (kill-safe) ----
    log_publish("pending", channel)

    # ---- title / description / tags (main.py jaisa) ----
    title = analysis["title"]
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"

    desc = str(analysis["description"]).strip()
    hashtags = analysis.get("hashtags") or []
    if hashtags:
        desc = desc + "\n\n" + " ".join(hashtags[:12])

    seen, tags = set(), []
    for t in list(analysis.get("tags") or []) + list(cfg["youtube"].get("tags", [])):
        t = str(t).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    trimmed, total = [], 0
    for t in tags:
        if total + len(t) + 1 > 480:
            break
        trimmed.append(t)
        total += len(t) + 1
    tags = trimmed

    # ---- YouTube upload (channel 1) ----
    try:
        yt_id = upload_video(out, title, desc, tags, cfg["youtube"], channel=channel)
    except AuthError:
        # Token hi fail hua - upload shuru nahi hua. Pending entry hatao
        # taaki galti se slot covered na dikhe. Drive id add NAHI hoti,
        # isliye agli run me ye video dobara try hogi.
        print(f"  [ch{channel}] Upload auth fail - pending entry hata raha hoon "
              f"(agli run me dobara try hoga).")
        revert_pending_publish(channel)
        raise

    # id SIRF tabhi done-list me add karo jab upload ne sach me video id di ho.
    # (kisi bhi fail/adhoora upload par add NAHI hota - agli run me dobara try.)
    if not yt_id:
        raise RuntimeError(
            "Upload ne video id nahi di - drive_done me add nahi kar rahe "
            "(agli run me dobara try hoga)."
        )
    confirm_pending_publish(yt_id, channel)
    mark_done(done, entry.id, name, yt_id)
    print(f"  Upload ho gaya -> https://youtu.be/{yt_id}  (title: {title})")

    # ---- upload ke baad ek related auto-comment post karo ----
    # Comment kabhi upload/pipeline fail na kare - fail ho to sirf warning.
    try:
        comment_text = _build_comment(analysis)
        post_comment(yt_id, comment_text, cfg["youtube"], channel=channel)
    except Exception as e:  # noqa: BLE001 - comment sirf best-effort hai
        print(f"  [comment] WARNING: comment skip ({str(e)[:150]})")

    # disk bachao: bade (2-3 GB) videos turant delete kar do
    for p in (src, out):
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass
    return yt_id, title


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    channel = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    cfg = load_config()

    # channel ke secrets set nahi hain to quietly skip (fail nahi karna)
    if channel > 1 and not channel_configured(channel):
        print(f"[ch{channel}] Secrets set nahi hain - ye channel skip kar raha hoon.")
        sys.exit(0)

    # data/drive_done.json ensure karo (workflow ka commit step ise dhundhta hai)
    if not DONE_PATH.exists():
        save_done({})

    work = ROOT / "work"
    work.mkdir(exist_ok=True)

    print(f"Drive folder: {DRIVE_FOLDER_URL}")
    videos = list_drive_videos()
    print(f"Drive folder me {len(videos)} video file(s) mili.")

    done = load_done()
    new_videos = [v for v in videos if v.id not in done]
    print(f"Unme se {len(new_videos)} nayi (pehle process nahi hui) hain.")

    # ---- title-based dedupe: same title (norm) pehle upload ho chuki ho to
    # skip karo - chahe us video ki file id nayi ho. ----
    fresh_videos = []
    for v in new_videos:
        title = Path(v.path).stem
        print(f"  Title: {title}")
        if title_already_done(done, title):
            print(f"  '{title}' - title pehle upload ho chuki hai - skip")
            continue
        fresh_videos.append(v)
    new_videos = fresh_videos

    if not new_videos:
        print("Process karne ke liye koi nayi video nahi - bas, exit.")
        return

    results = []
    attempted = 0
    for entry in new_videos:
        # Ek run me sirf MAX_PER_RUN video (default 1). Limit pahunch gaye to
        # baaki videos agli run me - workflow FAIL nahi hota (exit 0).
        if attempted >= MAX_PER_RUN:
            print(f"  MAX_PER_RUN={MAX_PER_RUN} pahunch gaya - baaki videos agli run me.")
            break
        attempted += 1
        name = Path(entry.path).name
        try:
            yt_id, title = process_one(entry, cfg, channel, work, done)
            results.append((name, "OK", f"https://youtu.be/{yt_id}", title))
        except AuthError:
            # channel creds hi kharab - aage try karna bekar hai
            print(f"[ch{channel}] Auth fail - baaki videos chhod raha hoon. "
                  f"Secrets (YT_*) check karo.")
            results.append((name, "FAIL", "auth error", ""))
            break
        except Exception as e:  # noqa: BLE001 - ek video fail ho to baaki chalti rahein
            print(f"  ERROR [{name}]: {e}")
            results.append((name, "FAIL", str(e)[:200], ""))

    # ---- summary ----
    print("\n================ RESULT ================")
    ok = 0
    for name, status, info, title in results:
        if status == "OK":
            ok += 1
            print(f"  [OK]   {name}  ->  {info}   | {title}")
        else:
            print(f"  [FAIL] {name}  ->  {info}")
    print(f"Total: {ok}/{len(results)} upload ho gayi.")
    remaining = len(new_videos) - attempted
    print(f"Is run me {attempted} video process hui, {remaining} baaki hain (agli run me).")
    if ok < len(results):
        # koi video fail hui - non-zero exit (workflow red ho, par
        # drive_done.json phir bhi commit hoga kyunki step if: always())
        sys.exit(1)


if __name__ == "__main__":
    main()
