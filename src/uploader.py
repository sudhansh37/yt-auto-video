"""YouTube Data API v3 se Shorts upload (requests-based, koi heavy library nahi).

Ye repo ke pehle se lage secrets use karta hai:
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
(purane youtube_upload.py wala proven approach - wahi resumable upload).
"""
import os

import requests

API_BASE = "https://www.googleapis.com"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def _get_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Env/secret '{name}' set nahi hai "
            f"(repo secrets me YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN hone chahiye)."
        )
    return value


def _refresh_access_token():
    """Refresh token se naya access token lo."""
    resp = requests.post(
        f"{API_BASE}/oauth2/v4/token",
        data={
            "client_id": _get_env("YT_CLIENT_ID"),
            "client_secret": _get_env("YT_CLIENT_SECRET"),
            "refresh_token": _get_env("YT_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["access_token"]


def upload_video(video_path, title, description, tags, youtube_cfg):
    """Video YouTube pe upload karke video id return karo."""
    access_token = _refresh_access_token()
    print("  [YT] Access token mila, upload start...")

    headers = {"Authorization": f"Bearer {access_token}"}

    # Step 1: resumable upload initialize
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500] if isinstance(tags, list) else [],
            "categoryId": str(youtube_cfg.get("category_id", "27")),
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi",
        },
        "status": {
            "privacyStatus": youtube_cfg.get("privacy", "public"),
            "selfDeclaredMadeForKids": False,
            # YouTube Studio ka "AI use" disclosure (Attributes section) -
            # upload ke waqt hi automatically Yes ho jata hai.
            # Band karna ho to config.yaml me youtube.ai_disclosure: false
            "containsSyntheticMedia": bool(youtube_cfg.get("ai_disclosure", True)),
        },
    }
    init_resp = requests.post(
        f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
        headers={**headers, "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    if init_resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Upload init error {init_resp.status_code}: {init_resp.text[:300]}"
        )
    upload_url = init_resp.headers.get("Location")
    if not upload_url:
        raise RuntimeError("Upload URL nahi mila (Location header missing).")

    # Step 2: video file PUT karo
    with open(video_path, "rb") as f:
        video_data = f.read()

    put_resp = requests.put(
        upload_url,
        headers={**headers, "Content-Type": "video/*"},
        data=video_data,
        timeout=600,
    )
    if put_resp.status_code in (200, 201):
        video_id = put_resp.json()["id"]
        print(f"  [YT] Upload success! https://youtu.be/{video_id}")
        return video_id
    raise RuntimeError(
        f"Upload error {put_resp.status_code}: {put_resp.text[:300]}"
    )
