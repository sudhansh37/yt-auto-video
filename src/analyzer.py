"""
Gemini video analysis -> Hindi narration script + title + description.

Video Gemini Files API se upload hoti hai, phir model se JSON response
manga jata hai: {"title", "description", "script"}
 - script   : Devanagari Hindi, video ki duration se match (TTS bolti hai)
              aur editor isi se time-synced captions banata hai

CRASH FIX (503 "high demand" ke liye):
  - har model ke liye 4 attempts (beech me 15/30/60s wait)
  - fail hone pe fallback models ki chain (flash + pro)
  - 404 (model retired) par retry waste nahi karte - seedha agla model
  - poora din Gemini down rahe to run fail hota hai, lekin watchdog har
    30 min me khud dobara try karta rehta hai - video der se sahi publish
    ho jaati hai

NOTE (Sep 2026): gemini-2.5-flash / 2.5-pro retire ho chuke hain (404).
Ab fallback chain: 3.6-flash -> flash-latest -> 3.5-flash -> 3.1-pro-preview.
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
# (pehle saare flash - fast/cheap, aakhir me pro - slow lekin available)
FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
]

# har model ke attempts ke beech ka wait (seconds) - exponential
ATTEMPT_WAITS = [15, 30, 60]


def _validate(analysis):
    for key in ("title", "description", "script"):
        if key not in analysis or not str(analysis[key]).strip():
            raise ValueError(f"Gemini response me '{key}' missing/khali hai.")


def _is_permanent_error(err_str):
    """404 (model retired) / invalid key - in par retry bekar hai."""
    return (
        "404" in err_str
        or "NOT_FOUND" in err_str
        or "not available" in err_str
        or "API key not valid" in err_str
    )


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
        for attempt in range(1, len(ATTEMPT_WAITS) + 2):   # 4 attempts
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
                # 404/retired model par retry waste hai - agla model
                if _is_permanent_error(str(e)):
                    print(f"  {model} available nahi hai - agla fallback model...")
                    break
                wait = ATTEMPT_WAITS[min(attempt - 1, len(ATTEMPT_WAITS) - 1)]
                print(f"  WARNING: Gemini {model} attempt {attempt} fail: {e}")
                print(f"           {wait}s wait karke retry...")
                time.sleep(wait)
        else:
            print(f"  {model} bhi fail - agla fallback model try karte hain...")

    raise RuntimeError(
        f"Gemini analysis fail hua (sab models/moves try ho gaye): {last_err}"
    )
