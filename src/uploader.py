"""YouTube Data API v3 se Shorts upload (requests-based, koi heavy library nahi).

MULTI-CHANNEL support:
  Channel 1: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
  Channel 2: YT_CLIENT_ID_2, YT_CLIENT_SECRET_2, YT_REFRESH_TOKEN_2
  (3, 4... bhi aise hi _3, _4 suffix se chalega)

DAILY CAP (over-upload se bachav):
  count_today_uploads(channel) YouTube se hi poochta hai ki aaj (IST) US
  channel pe kitni videos upload ho chuki hain. Ye git/publish_log se
  INDEPENDENT hai, isliye chahe log commit fail ho ya run kill ho jaye -
  duplicate upload ROKA ja sakta hai. Cap har channel ke liye alag hai
  (default 2/day per channel).
"""
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

import requests

API_BASE = "https://www.googleapis.com"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
IST = ZoneInfo("Asia/Kolkata")


def _env_suffix(channel):
    """Channel 1 -> '' (purane naam), channel 2 -> '_2', ..."""
    return "" if channel == 1 else f"_{channel}"


def _get_env(name, channel=1):
    """Channel ke hisaab se env/secret padho (YT_CLIENT_ID / YT_CLIENT_ID_2)."""
    value = os.environ.get(name + _env_suffix(channel))
    if not value:
        raise RuntimeError(
            f"Env/secret '{name}{_env_suffix(channel)}' set nahi hai "
            f"(repo secrets me {name} / {name}_2 hone chahiye)."
        )
    return value


def channel_configured(channel):
    """Is channel ke secrets set hain? (watchdog/main isse use karte hain)"""
    return all(
        os.environ.get(n + _env_suffix(channel))
        for n in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
    )


def _refresh_access_token(channel=1):
    """Refresh token se naya access token lo (is channel ke secrets se)."""
    resp = requests.post(
        f"{API_BASE}/oauth2/v4/token",
        data={
            "client_id": _get_env("YT_CLIENT_ID", channel),
            "client_secret": _get_env("YT_CLIENT_SECRET", channel),
            "refresh_token": _get_env("YT_REFRESH_TOKEN", channel),
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["access_token"]


def count_today_uploads(channel=1, max_pages=4):
    """AAJ (IST) is channel pe kitni videos upload ho chuki hain? (int)

    YouTube search API (forMine=true, order=date) se latest videos leta
    hai aur aaj ki IST date wali entries ginta hai. Ye SABSE reliable
    source of truth hai - publish_log.json commit fail ho jaye tab bhi
    duplicate upload nahi hoga.
    """
    token = _refresh_access_token(channel)
    today = datetime.now(IST).date()
    start_of_day = datetime.combine(today, time.min, IST)
    # RFC 3339: 2026-09-24T00:00:00+05:30
    published_after = start_of_day.isoformat()

    count = 0
    page_token = None
    for _ in range(max_pages):
        params = {
            "part": "snippet",
            "forMine": "true",
            "type": "video",
            "order": "date",
            "maxResults": 50,
            "publishedAfter": published_after,
        }
        if page_token:
            params["pageToken"] = page_token
        resp = requests.get(
            f"{API_BASE}/youtube/v3/search",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"YouTube search error {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        for item in data.get("items", []):
            try:
                published = datetime.fromisoformat(
                    item["snippet"]["publishedAt"].replace("Z", "+00:00")
                ).astimezone(IST)
            except (KeyError, ValueError):
                continue
            if published.date() == today:
                count += 1
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return count


def upload_video(video_path, title, description, tags, youtube_cfg, channel=1):
    """Video is channel ke account se YouTube pe upload karke video id do."""
    access_token = _refresh_access_token(channel)
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
