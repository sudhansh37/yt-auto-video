"""Gemini video analysis -> Hindi narration script + title + description.

Video Gemini Files API se upload hoti hai, phir model se JSON response
manga jata hai: {"title": ..., "description": ..., "script": ...}
Script Devanagari Hindi me hoti hai, video ki duration se match karti hui.
"""
import json
import os
import time

from google import genai
from google.genai import types

PROMPT = """\
You are a professional Hindi YouTube Shorts narrator and scriptwriter.

Watch this video carefully. It is roughly {duration} seconds long.
Write a Hindi narration script (in Devanagari script, natural spoken Hindi,
simple aur engaging bhasha) jo video me jo dikhaya/sunaya gaya hai use
start se end tak explain kare, video ke pace ke saath.

Rules:
- Script video me jo ACTUALLY dikh raha hai usi par based ho - kuch mat banao.
- Spoken Hindi ~2.5 words per second hoti hai, isliye target ~{words} words.
- Pehle 2 second me ek strong attention-grabbing hook line.
- End me ek soft CTA (jaise "aisi hi videos ke liye follow karo").
- IMPORTANT: script ek hi continuous flow me likho - koi ellipses (...),
  dashes (-, --), pause markers ya line breaks NAHI. Chhoti tight sentences
  jo bina gap ke ek saans me boli ja sakein. Voice-over robotic na lage.

Return ONLY a JSON object with exactly these keys:
{{
  "title": "catchy Hindi title, max 90 characters",
  "description": "1-2 line Hindi description + 3-5 hashtags",
  "script": "poora Hindi narration script, Devanagari me"
}}
"""


def analyze_video(video_path, duration_s, gemini_cfg):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY env/secret set nahi hai.")

    client = genai.Client(api_key=api_key)

    # video upload + processing complete hone ka wait
    print("  Gemini ko video upload ho rahi hai (ye kuch second me le sakti hai)...")
    f = client.files.upload(file=str(video_path))
    while f.state.name == "PROCESSING":
        time.sleep(3)
        f = client.files.get(name=f.name)
    if f.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini file state unexpected: {f.state.name}")

    model = gemini_cfg.get("model", "gemini-3.6-flash")
    prompt = PROMPT.format(duration=int(duration_s), words=int(duration_s * 2.5))

    resp = client.models.generate_content(
        model=model,
        contents=[prompt, f],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    analysis = json.loads(resp.text)
    for key in ("title", "description", "script"):
        if key not in analysis or not analysis[key].strip():
            raise RuntimeError(f"Gemini response me '{key}' missing/khali hai.")
    return analysis
