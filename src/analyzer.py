"""
Gemini video analysis -> Hindi narration script + title + description.

Video Gemini Files API se upload hoti hai, phir model se JSON response
manga jata hai: {"title", "description", "script"}
 - script   : Devanagari Hindi, video ki duration se match (TTS bolti hai)
              aur editor isi se time-synced captions banata hai
 - captions : wahi script ka HINGLISH (Roman letters) version - isse
              on-screen captions banti hain (Devanagari nahi). Model
              na de to editor script par fallback kar deta hai.

GEMINI FALLBACK KEYS:
  - GEMINI_API_KEY pehle try hota hai; uska quota khatam ho jaye
    (429 PerDay) to GEMINI_API_KEY_2 automatic use hota hai
    (video dobara upload hoti hai naye key se, baaki sab same)

CRASH FIX (503 "high demand" / 429 "quota" ke liye):
  - har model ke liye 4 attempts (beech me 15/30/60s wait)
  - fail hone pe fallback models ki chain (flash + pro)
  - 404 (model retired) par retry waste nahi karte - seedha agla model
  - 429 "PerDay" (daily quota khatam) par bhi retry bekar - agla model
    (free tier me har model ke 20 requests/din hote hain)
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
  "script": "poora Hindi narration script, Devanagari me",
  "captions": "Wahi script ka HINGLISH version - Latin/Roman letters me likha hua (jaise: 'yeh dekho kya ho raha hai', 'aap yeh dekh sakte hain'). Sirf Latin letters use karo, Devanagari letters BILKUL NAHI. Same words, same order - bas script ko Roman me likha hua. Ye on-screen captions ke liye hai."
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
    # 'captions' (Hinglish) optional hai - na mile to editor Devanagari
    # script par fallback kar dega, run fail nahi hota.
    if not str(analysis.get("captions") or "").strip():
        analysis["captions"] = ""


def _is_permanent_error(err_str):
    """404 (model retired) / invalid key - in par retry bekar hai."""
    return (
        "404" in err_str
        or "NOT_FOUND" in err_str
        or "not available" in err_str
        or "API key not valid" in err_str
    )


def _api_keys():
    """Sab available Gemini keys (pehla primary, dusra fallback)."""
    keys = [os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_API_KEY_2")]
    keys = [k for k in keys if k]
    if not keys:
        raise RuntimeError("GEMINI_API_KEY env/secret set nahi hai.")
    return keys


def _try_models(client, file_obj, prompt, primary):
    """Ek key (client) ke saath saare models try karo. analysis ya raise."""
    models = [primary] + [m for m in FALLBACK_MODELS if m != primary]

    last_err = None
    for model in models:
        for attempt in range(1, len(ATTEMPT_WAITS) + 2):   # 4 attempts
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=[prompt, file_obj],
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
                err_str = str(e)
                # 404/retired model par retry waste hai - agla model
                if _is_permanent_error(err_str):
                    print(f"  {model} available nahi hai - agla fallback model...")
                    break
                # DAILY quota khatam (PerDay) - aaj is model par retry
                # bekar hai, seedha agla model try karo
                if "RESOURCE_EXHAUSTED" in err_str and "PerDay" in err_str:
                    print(f"  {model} ka DAILY quota khatam - agla fallback model...")
                    break
                wait = ATTEMPT_WAITS[min(attempt - 1, len(ATTEMPT_WAITS) - 1)]
                print(f"  WARNING: Gemini {model} attempt {attempt} fail: {e}")
                print(f"           {wait}s wait karke retry...")
                time.sleep(wait)
        else:
            print(f"  {model} bhi fail - agla fallback model try karte hain...")

    raise RuntimeError(
        f"Is API key pe saare models fail ho gaye: {last_err}"
    )


def analyze_video(video_path, duration_s, gemini_cfg):
    keys = _api_keys()
    last_err = None

    for k_idx, api_key in enumerate(keys):
        client = genai.Client(api_key=api_key)

        # video upload + processing complete hone ka wait
        key_label = f"key {k_idx + 1}/{len(keys)}"
        print(f"  Gemini ko video upload ho rahi hai ({key_label})...")
        try:
            f = client.files.upload(file=str(video_path))
            while f.state.name == "PROCESSING":
                time.sleep(3)
                f = client.files.get(name=f.name)
            if f.state.name != "ACTIVE":
                raise RuntimeError(f"Gemini file state unexpected: {f.state.name}")
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001 - upload fail = agli key try
            last_err = e
            print(f"  WARNING: key {k_idx + 1} se upload fail ({e}) - agli key...")
            continue

        prompt = PROMPT.format(duration=int(duration_s), words=int(duration_s * 2.5))
        primary = gemini_cfg.get("model", "gemini-3.6-flash")

        try:
            return _try_models(client, f, prompt, primary)
        except Exception as e:  # noqa: BLE001 - ye key over, agli key
            last_err = e
            if k_idx + 1 < len(keys):
                print(f"  WARNING: key {k_idx + 1} se analysis fail - "
                      f"fallback key try kar rahe hain...")
                continue
            raise

    raise RuntimeError(
        f"Gemini analysis fail hua (saare keys/models try ho gaye): {last_err}"
    )
