"""
Main Entry Point - GitHub Actions ke liye
==========================================
1. topics.txt se ek topic pick karta hai
2. Video banata hai (pipeline.py)
3. YouTube pe upload karta hai (youtube_upload.py)
"""

import os
import random
import sys

from pipeline import generate_video
from youtube_upload import upload_to_youtube


def pick_topic():
    """topics.txt se random topic select karta hai"""
    topics_file = os.path.join(os.path.dirname(__file__), "topics.txt")
    
    if not os.path.exists(topics_file):
        return "5 interesting facts about India"
    
    with open(topics_file, "r", encoding="utf-8") as f:
        topics = [line.strip() for line in f.readlines() if line.strip() and not line.startswith("#")]
    
    if not topics:
        return "5 interesting facts about India"
    
    return random.choice(topics)


def main():
    # Environment variables (GitHub Secrets se aayenge)
    GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
    HF_TOKEN = os.environ.get("HF_TOKEN", "")
    YT_CLIENT_ID = os.environ.get("YT_CLIENT_ID", "")
    YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET", "")
    YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN", "")
    
    # Voice setting
    VOICE = os.environ.get("VOICE", "hi-IN-MadhurNeural")
    
    # Check API keys
    if not GEMINI_KEY:
        print("❌ GEMINI_API_KEY environment variable nahi mili!")
        sys.exit(1)
    if not HF_TOKEN:
        print("❌ HF_TOKEN environment variable nahi mili!")
        sys.exit(1)
    
    # Pick topic
    topic = pick_topic()
    print(f"\n🎬 Topic selected: {topic}\n")
    print("=" * 50)
    
    # Step 1: Video generate karo
    video_path, info = generate_video(topic, GEMINI_KEY, HF_TOKEN, VOICE)
    
    if not video_path or not info:
        print("❌ Video generation fail ho gaya!")
        sys.exit(1)
    
    print("\n" + "=" * 50)
    print(f"📊 Video Info:")
    print(f"   Duration: {info['duration']:.1f} sec")
    print(f"   Title: {info['title']}")
    print(f"   Scenes: {len(info['scenes'])}")
    print("=" * 50)
    
    # Step 2: YouTube pe upload karo (agar keys hain)
    if YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN:
        print("\n📤 YouTube pe upload ho raha hai...")
        
        result = upload_to_youtube(
            video_path=video_path,
            title=info["title"],
            description=info["description"],
            tags=info["tags"],
            client_id=YT_CLIENT_ID,
            client_secret=YT_CLIENT_SECRET,
            refresh_token=YT_REFRESH_TOKEN,
            privacy="public"
        )
        
        if result:
            print(f"\n🎉 SUCCESS! Video YouTube pe upload ho gayi!")
            print(f"   URL: {result['url']}")
        else:
            print("\n⚠️ YouTube upload fail ho gaya, lekin video ban gayi hai!")
            print(f"   Video path: {video_path}")
    else:
        print("\n⚠️ YouTube credentials nahi mili - video bani lekin upload nahi hua.")
        print("   GitHub Secrets me YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN daalo.")
        print(f"   Video path: {video_path}")
    
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
