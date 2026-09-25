"""
TTS module - Hindi voice banata hai.

Providers (config.yaml me choose karo):
  - "ttsfree"    : TTSFree.com ka API (premium). Secret 'ttsfree' me apikey.
                   Fail hone pe fallback chain: IndicF5 -> Parler -> edge-tts
  - "indicf5"    : AI4Bharat IndicF5 (HuggingFace Space) - FREE, near-human
  - "edge_tts"   : Microsoft Edge TTS (free, no key) - Madhur voice
  - "custom_http": apna koi bhi TTS API (template config.yaml me)
  - "sarvam"     : Sarvam AI TTS example

NO-GAP (natural voice):
  clean_for_speech() sirf faltu cheezein hataata hai (ellipses, dashes,
  emojis, repeated punctuation, line breaks) - sentence punctuation
  RAKHTA hai, kyunki natural intonation wahi se aati hai. Voice ke
  beech me koi lamba gap nahi rehta.
"""
import asyncio
import copy
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests


def clean_for_speech(text):
    """TTS text se faltu pause banane wali cheezein hata do.

    IMPORTANT: sentence-ending punctuation (। . ! ?) ko rakhna hai -
    natural TTS isi se intonation aur natural pauses banata hai.
    Sirf wahi cheezein hatao jo LAMBE silence gap banati hain.
    """
    # ellipses (..., ...) -> single full stop (ye sabse lamba gap banata hai)
    text = re.sub(r"\.{2,}", ".", text)
    text = text.replace("\u2026", ".")
    # dashes (-, –, —) jo beech me pause banate hain
    text = re.sub(r"\s*[-\u2013\u2014]\s*", " ", text)
    # emojis hatao (kuch TTS engines inpe atak jaate hain)
    text = re.sub("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", text)
    # repeated punctuation -> single
    text = re.sub(r"([!?\u0964.,])\1+", r"\1", text)
    # extra commas (har comma ek pause hota hai)
    text = re.sub(r",\s*,", ",", text)
    # newlines / multiple spaces -> single space (paragraph gap hata ke flow me)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _ffmpeg_exe():
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _ttsfree_key():
    for name in ("ttsfree", "TTSFREE", "TTSFREE_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    raise RuntimeError(
        "TTSFree apikey set nahi hai - repo secrets me 'ttsfree' naam se "
        "key daalo (ttsfree.com -> Profile -> API Key)."
    )


def _concat_files(parts, out_path, reencode):
    """Audio part files ko ffmpeg concat se jodo."""
    list_file = Path(out_path).parent / "tts_parts.txt"
    listfile.write_text(
        "\n".join(f"file '{p}'" for p in parts), encoding="utf-8"
    )
    cmd = [_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0",
          "-i", str(listfile)]
    if reencode:
        cmd += ["-c:a", "libmp3lame", "-q:a", "2"]
    else:
        cmd += ["-c", "copy"]
    cmd += [str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)


def synthesize(text, tts_cfg, out_path):
    """Script ko audio file me convert karo. Output path return hota hai."""
    out_path = Path(out_path)
    # sabse pehle: no-gap cleanup (har provider pe apply hota hai)
    text = clean_for_speech(text)
    if not text:
        raise RuntimeError("TTS ke liye script khali hai!")

    provider = tts_cfg.get("provider", "gemini")

    # fallback chain - jo provider config me hai wahi pehle try hota hai
    if provider == "gemini":
        chain = [
            ("Gemini", lambda: _gemini_tts(text, tts_cfg.get("gemini", {}), out_path)),
            ("TTSFree", lambda: _ttsfree(text, tts_cfg.get("ttsfree", {}), out_path)),
            ("IndicF5", lambda: _indicf5(text, tts_cfg.get("indicf5", {}), out_path)),
            ("Parler", lambda: _parler(text, {}, out_path)),
            ("edge-tts", lambda: _edge_tts(text, tts_cfg.get("edge_tts", {}), out_path)),
        ]
    elif provider == "ttsfree":
        chain = [
            ("TTSFree", lambda: _ttsfree(text, tts_cfg.get("ttsfree", {}), out_path)),
            ("Gemini", lambda: _gemini_tts(text, tts_cfg.get("gemini", {}), out_path)),
            ("IndicF5", lambda: _indicf5(text, tts_cfg.get("indicf5", {}), out_path)),
            ("Parler", lambda: _parler(text, {}, out_path)),
            ("edge-tts", lambda: _edge_tts(text, tts_cfg.get("edge_tts", {}), out_path)),
        ]
    elif provider == "indicf5":
        chain = [
            ("IndicF5", lambda: _indicf5(text, tts_cfg.get("indicf5", {}), out_path)),
            ("Parler", lambda: _parler(text, {}, out_path)),
            ("edge-tts", lambda: _edge_tts(text, tts_cfg.get("edge_tts", {}), out_path)),
        ]
    elif provider == "edge_tts":
        return _edge_tts(text, tts_cfg.get("edge_tts", {}), out_path)
    elif provider == "custom_http":
        return _custom_http(text, tts_cfg.get("custom_http", {}), out_path)
    elif provider == "sarvam":
        return _sarvam(text, tts_cfg.get("sarvam", {}), out_path)
    else:
        raise ValueError(f"Unknown TTS provider: {provider!r}")

    last_err = None
    for name, fn in chain:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - agla fallback
            last_err = e
            print(f"WARNING: {name} TTS fail hua ({str(e)[:200]})")
            print(f"         agla fallback try kar rahe hain...")
    raise RuntimeError(f"Saare TTS providers fail ho gaye: {last_err}")


# ----------------------------------------------------------------------------
# Gemini TTS - Google ka native speech model (GEMINI_API_KEY se, naya key nahi)
#
# Model se 24kHz 16-bit mono PCM aata hai (WAV me wrap karte hain).
# Style prompt se tone control hota hai, voice_name se speaker.
# Male voices:   Puck (energetic), Charon (informative), Fenrir (excitable),
#                Orus (firm)
# Female voices: Korec Aoede, Leda, Zephyr
# ------------------------------------------------------------------------------

def _write_pcm_wav(path, pcm, sample_rate=24000):
    """Raw 16-bit mono PCM ko WAV file me wrap karo."""
    import wav

    with wav.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsample()
        w.setfs