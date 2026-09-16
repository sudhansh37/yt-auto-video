"""yt-dlp helpers - channel ki shorts list nikalna + download.

QUALITY NOTE: vertical (9:16) videos me 1080p ka matlab WIDTH 1080 hota hai
(height 1920). Isliye format me width<=1080 use karte hain - height<=1080
rakhte to sirf 607px ki giri hui quality milti!

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


def _download_attempt(video_id, out_dir, client):
    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        # width<=1080 = vertical video ka full HD (1080x1920)
        "format": "bv*[width<=1080]+ba/b[width<=1080]/b",
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        # EJS challenge solver - n-challenge solve karke saare formats milte hain
        "remote_components": ["ejs:github"],
        "cookiefile": _write_cookies_if_any(),
    }
    if client:
        opts["extractor_args"] = {"youtube": {"player_client": client}}
    opts = {k: v for k, v in opts.items() if v}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    for p in sorted(out_dir.glob("source.*")):
        return p
    return None


def download_video(video_id, out_dir):
    """Ek video download karo - pehle default client, phir fallbacks try karo."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    last_err = None
    for client in PLAYER_CLIENT_FALLBACKS:
        try:
            path = _download_attempt(video_id, out_dir, client)
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
