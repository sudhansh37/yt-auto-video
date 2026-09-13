"""
YouTube Auto-Upload Script
==========================
YouTube Data API v3 se video upload karta hai.
Refresh token use karta hai (no browser login needed every time).
"""

import os
import json
import requests
import time


def refresh_access_token(client_id, client_secret, refresh_token):
    """Refresh token se naya access token lo"""
    url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    response = requests.post(url, data=data, timeout=30)
    
    if response.status_code != 200:
        print(f"Token refresh error: {response.text}")
        return None
    
    return response.json()["access_token"]


def upload_to_youtube(video_path, title, description, tags, 
                     client_id, client_secret, refresh_token,
                     privacy="public", category_id="22"):
    """
    YouTube pe video upload karo.
    
    category_id options:
    - 22: People & Blogs
    - 25: News & Politics
    - 27: Education
    - 28: Science & Technology
    - 24: Entertainment
    """
    
    # Step 1: Access token refresh karo
    print("[YT] Access token refresh ho raha hai...")
    access_token = refresh_access_token(client_id, client_secret, refresh_token)
    if not access_token:
        print("[YT] ❌ Access token nahi mila!")
        return None
    
    # Step 2: Upload initialize karo (resumable upload)
    print("[YT] Upload start ho raha hai...")
    
    file_size = os.path.getsize(video_path)
    
    # Metadata + initial request
    url = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500] if isinstance(tags, list) else [],
            "categoryId": category_id,
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi"
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False
        }
    }
    
    response = requests.post(url, headers=headers, json=body, timeout=30)
    
    if response.status_code not in [200, 201]:
        print(f"[YT] ❌ Upload init error: {response.status_code} - {response.text[:300]}")
        return None
    
    # Get upload URL
    upload_url = response.headers.get("Location")
    if not upload_url:
        print("[YT] ❌ Upload URL nahi mila!")
        return None
    
    # Step 3: Video file upload karo (chunked)
    print(f"[YT] Video upload ho rahi hai ({file_size / (1024*1024):.1f} MB)...")
    
    with open(video_path, "rb") as f:
        video_data = f.read()
    
    headers_upload = {
        "Content-Type": "video/*",
        "Content-Length": str(len(video_data))
    }
    
    response = requests.put(upload_url, headers=headers_upload, data=video_data, timeout=300)
    
    if response.status_code in [200, 201]:
        video_id = response.json().get("id")
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"[YT] ✅ Upload successful!")
        print(f"[YT] Video URL: {video_url}")
        print(f"[YT] Video ID: {video_id}")
        return {
            "video_id": video_id,
            "url": video_url,
            "title": title
        }
    else:
        print(f"[YT] ❌ Upload error: {response.status_code} - {response.text[:300]}")
        return None
