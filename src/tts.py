"""TTS module - Hindi voice banata hai.

Providers (config.yaml me choose karo):
  - "edge_tts"    : Microsoft Edge TTS (default - free, koi API key nahi)
  - "custom_http" : apna koi bhi TTS API (template config.yaml me)
  - "sarvam"      : Sarvam AI TTS example

NO-GAP FIX (robotic voice / spaces ka ilaaj):
  Voice ke beech spaces isliye aate hain:
    1. Text me ellipses (...), dashes, extra commas, line breaks hote hain
    2. Sentence-end (purna viram "," / ".") pe TTS lambi pause leta hai
  clean_for_speech() in sab ko fix karta hai:
    - faltu pause cheezein hata deta hai
    - sentence-enders ko comma me badal deta hai (pause chhoti ho jati hai)
  + rate thodi tez rakhte hain, taki bol ek flow me chale.
"""
import asyncio
import copy
import os
import re
from pathlib import Path

import requests


def clean_for_speech(text):
    """TTS text se faltu pause banane wali cheezein hata do."""
    # ellipses (..., …) -> single full stop (ye TTS me sabse lambi pause banata hai)
    text = re.sub(r"\.{2,}", ".", text)
    text = text.replace("…", ".")
    # dashes (-, –, —) jo beech me pause banate hain
    text = re.sub(r"\s*[-–—]\s*", " ", text)
    # emojis hatao (kuch TTS engines inpe atak jaate hain)
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", text)
    # repeated punctuation -> single
    text = re.sub(r"([!?।,])\1+", r"\1", text)
    # extra commas (har comma ek pause hota hai)
    text = re.sub(r",\s*,", ",", text)
    # newlines / multiple spaces -> single space (paragraph gap hata ke flow me)
    text = re.sub(r"\s+", " ", text)
    # NO-GAP: sentence end (। aur .) ko comma bana do -
    # isse lambi pause chhoti ho jati hai aur bol ek flow me chalta hai
    text = text.replace("।", ",").replace(".", ",")
    # ab double commas/punctuation jama na ho
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r",\s*([!?])", r" \1", text)
    return text.strip(" ,").strip()


def synthesize(text, tts_cfg, out_path):
    """Script ko audio file me convert karo. Output path return hota hai."""
    out_path = Path(out_path)
    # sabse pehle: no-gap cleanup (har provider pe apply hota hai)
    text = clean_for_speech(text)
    if not text:
        raise RuntimeError("TTS ke liye script khali hai!")

    provider = tts_cfg.get("provider", "edge_tts")
    if provider == "edge_tts":
        return _edge_tts(text, tts_cfg.get("edge_tts", {}), out_path)
    if provider == "custom_http":
        return _custom_http(text, tts_cfg.get("custom_http", {}), out_path)
    if provider == "sarvam":
        return _sarvam(text, tts_cfg.get("sarvam", {}), out_path)
    raise ValueError(f"Unknown TTS provider: {provider!r}")


async def _edge_tts_async(text, cfg, out_path):
    import edge_tts

    # VOICE env (workflow se aata hai) config ko override karta hai
    voice = os.environ.get("VOICE") or cfg.get("voice", "hi-IN-MadhurNeural")
    rate = cfg.get("rate", "+12%")
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(out_path))


def _edge_tts(text, cfg, out_path):
    asyncio.run(_edge_tts_async(text, cfg, out_path))
    return out_path


def _get_api_key():
    key = os.environ.get("TTS_API_KEY")
    if not key:
        raise RuntimeError("TTS_API_KEY env/secret set nahi hai.")
    return key


def _custom_http(text, cfg, out_path):
    """Generic template - apna koi bhi TTS API yahan plug karo (code change zero)."""
    headers = {"Content-Type": "application/json"}
    auth = cfg.get("auth_header", "")
    if auth:
        headers["Authorization"] = auth.replace("{{TTS_API_KEY}}", _get_api_key())

    body = copy.deepcopy(cfg.get("body", {}))
    for k, v in body.items():
        if isinstance(v, str):
            body[k] = v.replace("{text}", text)

    resp = requests.post(cfg["endpoint"], headers=headers, json=body, timeout=300)
    resp.raise_for_status()

    response_spec = cfg.get("response", "binary")
    if response_spec == "binary":
        out_path.write_bytes(resp.content)
    elif response_spec.startswith("base64_json:"):
        data = resp.json()
        for part in response_spec.split(":", 1)[1].split("."):
            data = data[part]
        if isinstance(data, str):
            data = __import__("base64").b64decode(data)
        out_path.write_bytes(data)
    else:
        raise ValueError(f"response spec samajh nahi aaya: {response_spec!r}")
    return out_path


def _sarvam(text, cfg, out_path):
    """Sarvam AI TTS example implementation."""
    import base64

    resp = requests.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={"api-subscription-key": _get_api_key()},
        json={
            "text": text,
            "speaker": cfg.get("speaker", "meera"),
            "model": cfg.get("model", "bulbul-v2"),
            "speech_rate": cfg.get("speech_rate", 1.15),
        },
        timeout=300,
    )
    resp.raise_for_status()
    audio_b64 = resp.json()["audios"][0]
    out_path.write_bytes(base64.b64decode(audio_b64))
    return out_path
