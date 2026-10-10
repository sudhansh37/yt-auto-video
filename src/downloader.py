"""yt-dlp helpers - channel ki shorts list nikalna + download.

QUALITY NOTE: ab koi WIDTH CAP nahi hai - source ka BEST available stream
download hota hai (jaise 1080x1920, ya agar usse upar available ho to
1440x2560 / 2160x3840). Vertical (9:16) videos me resolution WIDTH se naapo
(1080p = 1080x1920) - height se naapne par giri hui quality milegi.

Cloud IPs (GitHub Actions) pe kabhi kabhi YouTube "Sign in to confirm
you're not a bot" dikha deta hai. Isliye:
  1. Agar YOUTUBE_COOKIES secret set ho (Netscape cookies.txt ka content),
     to wahi use hota hai - ye 100% reliable fix hai.
     (cookies export kaise kare: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
  2. Kuch alag player clients try karte hain (tv, tv_simply, ios, mweb)
  3. remote_components ejs:github -> YouTube ka n-challenge solve hota hai,
     warna formats missing ho jaate hain ("Only images are available").
"""
import os
import tempfile
import time
from pathlib import Path

import yt_dlp

PLAYER_CLIENT_FALLBACKS = (None, ["tv"], ["tv_simply"], ["ios"], ["mweb"])


def _write_cookies_if_any():
    """YOUTUBE_COOKIES secret (cookies.txt content) ho to temp file bana do."""
    cookies = os.environ.get("YOUTUBE_COOKIES")
    if not cookies:
        return None
    path = Path(tempfile.gettempdir()) / "yt_cookies.txt"
    path.write_text(cookies, encoding="utf-8")
    return str(path)


def list_short_ids(channel_url):
    """Channel ke shorts tab ki saari video ids (newest first order me)."""
    opts = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        # EJS challenge solver (GitHub se fetch hota hai) - iske bina
        # YouTube ka n-challenge solve nahi hota aur formats missing rehte hain
        "remote_components": ["ejs:github"],
        "cookiefile": _write_cookies_if_any(),
    }
    opts = {k: v for k, v in opts.items() if v}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    entries = info.get("entries") or []
    return [e["id"] for e in entries if e and e.get("id")]


def _download_attempt(video_id, out_dir, client, started=0.0):
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        # BEST available stream (koi width cap nahi) - source jo bhi sabse
        # acchi quality de, wahi download hoti hai (1080/1440/2160...)
        "format": "bv*+ba/b",
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        # EJS challenge solver - n-challenge solve karke saare formats milte hain
        "remote_components": ["ejs:github"],
        "cookiefile": _write_cookies_if_any(),
        # output file pehle se ho to bhi FORCE re-download
        "overwrites": True,
    }
    if client:
        opts["extractor_args"] = {"youtube": {"player_client": client}}
    opts = {k: v for k, v in opts.items() if v}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    for p in sorted(out_dir.glob("source.*")):
        # sirf FRESH file accept karo - download shuru hone se pehle ki
        # koi bachi hui STALE file reject karo (1s tolerance ke saath)
        if p.stat().st_mtime + 1 >= started:
            # CHHOTI file = download galat hua. YouTube ka SABR-only experiment
            # kabhi-kabhi sirf preview/storyboard deta hai (kuch KB) - usse
            # Gemini 'FAILED' keh deta hai. Aisi file reject karo taaki agla
            # player client try ho.
            if p.stat().st_size < 150_000:
                print(f"[downloader] WARNING: file bahut chhoti "
                      f"({p.stat().st_size} bytes) - ye asli video nahi hai, "
                      f"agla client try kar rahe hain")
                continue
            return p
        print(f"[downloader] WARNING: stale file reject ho rahi hai: {p}")
    return None


def download_video(video_id, out_dir):
    """Ek video download karo - pehle default client, phir fallbacks try karo.

    STALE-FILE GUARD: channel 1 aur channel 2 ek hi 'work' folder share
    karte hain. yt-dlp pehle se maujood output file ko 'already downloaded'
    maan kar download SILENTLY SKIP kar deta hai - is wajah se ek hi video
    dono channels pe upload ho gayi thi (ch2 ne ch1 ki source.mp4 hi use
    kar li thi). Isliye download se PEHLE purani source.* files delete
    hoti hain, 'overwrites' force hota hai, aur file ka mtime verify hota
    hai ki wo abhi ki hi download hai.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # purani source files hata do (ch1/ch2 ya pichhli run ki)
    for old in out_dir.glob("source.*"):
        try:
            old.unlink()
        except OSError:
            pass

    started = time.time()
    last_err = None
    for client in PLAYER_CLIENT_FALLBACKS:
        try:
            path = _download_attempt(video_id, out_dir, client, started)
            if path:
                return path
        except Exception as e:  # noqa: BLE001 - fallback chain
            last_err = e
            continue

    raise RuntimeError(
        f"Download fail hua: {video_id}\nAakhri error: {last_err}\n"
        "Tip: GitHub Actions pe 'Sign in to confirm you're not a bot' aaye to "
        "browser se cookies.txt export karke repo secret 'YOUTUBE_COOKIES' me "
        "paste kar do."
    )
