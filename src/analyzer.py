"""
Gemini video analysis -> Hindi narration script + title + description.

Video Gemini Files API se upload hoti hai, phir model se JSON response
manga jata hai: {"title", "description", "script"}
 - script   : Devanagari Hindi, video ki duration se match (TTS bolti hai)

CRASH FIX: Gemini kabhi kabhi 503 "high demand" deta hai (temporary).
Isliye ab:
  - har model ke liye 3 attempts (beech me wait)
  - fail hone pe fallback models ki chain try hoti hai
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
  dashes (-, --), ya line breaks NAHI. Chhoti natural sentences.

Return ONLY a JSON object with exactly these keys:
{{
  "title": "catchy Hindi title (Devanagari), max 90 characters",
  "description": "2-3 line Hindi description (Devanagari) + neeche 8-10 hashtags mix karo: #shorts #facts #viral #hindifacts #amazingfacts ke saath video ke topic ke 4-5 specific hashtags",
  "script": "poora Hindi narration script, Devanagari me"
}}
"""

# 503 "high demand" fail hone pe ye fallback models try hote hain
FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]


def _validate(analysis):
    for key in ("title", "description", "script"):
        if key not in analysis or not str(analysis[key]).strip():
            raise ValueError(f"Gemini response me '{key}' missing/khali hai.")


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

    prompt = PROMPT.format(duration=int(duration_s), words=int(duration_s * 2.5))

    # primary model pehle, phir fallbacks (duplicate hata ke)
    primary = gemini_cfg.get("model", "gemini-3.6-flash")
    models = [primary] + [m for m in FALLBACK_MODELS if m != primary]

    last_err = None
    for model in models:
        for attempt in range(1, 4):   # har model ke 3 attempts
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=[prompt, f],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    ),
                )
                analysis = json.loads(resp.text)
                _validate(analysis)
                if model != primary:
                    print(f"  (fallback model se aaya: {model})")
                return analysis
            except Exception as e:  # noqa: BLE001 - retry chain
                last_err = e
                wait = 15 * attempt
                print(f"  WARNING: Gemini {model} attempt {attempt}/3 fail: {e}")
                print(f"           {wait}s wait karke retry...")
                time.sleep(wait)
        print(f"  {model} bhi fail - agla fallback model try karte hain...")

    raise RuntimeError(
        f"Gemini analysis fail hua (sab models/moves try ho gaye): {last_err}"
    )
