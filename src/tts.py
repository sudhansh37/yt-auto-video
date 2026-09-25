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
    list_file.write_text(
        "\n".join(f"file '{p}'" for p in parts), encoding="utf-8"
    )
    cmd = [_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0",
           "-i", str(list_file)]
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
# Female voices: Kore, Aoede, Leda, Zephyr
# ----------------------------------------------------------------------------

def _write_pcm_wav(path, pcm, sample_rate=24000):
    """Raw 16-bit mono PCM ko WAV file me wrap karo."""
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)


def _gemini_tts(text, cfg, out_path):
    """Gemini 2.5 TTS se natural Hindi voice banao.

    - GEMINI_API_KEY pehle; uska quota over ho to GEMINI_API_KEY_2
      automatic use hota hai (chunk generate ke dauran switch hota hai)
    - lambi script automatic chunks me jaati hai, phir WAV concat
    - fail hone pe fallback chain (ttsfree/IndicF5/edge-tts) chalti hai
    """
    import base64

    from google import genai
    from google.genai import types

    api_keys = [k for k in (os.environ.get("GEMINI_API_KEY"),
                            os.environ.get("GEMINI_API_KEY_2")) if k]
    if not api_keys:
        raise RuntimeError("GEMINI_API_KEY env/secret set nahi hai.")

    model = cfg.get("model", "gemini-2.5-flash-preview-tts")
    voice = cfg.get("voice", "Puck")          # male: Puck/Charon/Fenrir/Orus
    style = cfg.get(
        "style",
        "Say in an energetic, engaging and clear YouTube shorts narrator "
        "tone, speaking natural Hindi:",
    )

    client = genai.Client(api_key=api_keys[0])
    key_idx = 0

    def _generate(model, contents, config):
        """Generate karo; quota/key error pe agli key se retry."""
        nonlocal client, key_idx
        try:
            return client.models.generate_content(
                model=model, contents=contents, config=config
            )
        except Exception as e:  # noqa: BLE001
            err = str(e)
            quota_or_auth = any(s in err for s in (
                "RESOURCE_EXHAUSTED", "429", "quota",
                "API key not valid", "PERMISSION_DENIED",
            ))
            if quota_or_auth and key_idx + 1 < len(api_keys):
                key_idx += 1
                print(f"  [GeminiTTS] key {key_idx} quota/issue - fallback key se retry...")
                client = genai.Client(api_key=api_keys[key_idx])
                return client.models.generate_content(
                    model=model, contents=contents, config=config
                )
            raise

    chunks = _split_script(text, max_chars=1500)
    parts = []
    for i, chunk in enumerate(chunks):
        print(f"  [GeminiTTS] chunk {i + 1}/{len(chunks)} generate ho raha hai...")
        resp = _generate(
            model,
            f"{style}\n\n{chunk}",
            types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice
                        )
                    )
                ),
            ),
        )
        # response me inline audio (base64 PCM) aata hai
        data = resp.candidates[0].content.parts[0].inline_data
        raw = data.data
        if isinstance(raw, str):
            raw = base64.b64decode(raw)

        # sample rate mime_type se nikaalo (audio/L16;rate=24000), default 24k
        sample_rate = 24000
        mime = (data.mime_type or "")
        if "rate=" in mime:
            try:
                sample_rate = int(mime.split("rate=")[-1].split(";")[0])
            except ValueError:
                pass

        part = out_path.parent / f"gemini_part{i}.wav"
        _write_pcm_wav(part, raw, sample_rate)
        if part.stat().st_size < 2000:
            raise RuntimeError("Gemini TTS audio bahut chhota aaya")
        parts.append(part)

    out_wav = out_path.with_suffix(".wav")
    if len(parts) == 1:
        shutil.copy(parts[0], out_wav)
        return out_wav
    _concat_files(parts, out_wav, reencode=False)
    return out_wav


# ----------------------------------------------------------------------------
# TTSFree.com - premium TTS API
#
# API: POST https://ttsfree.com/api/v1/tts
#   headers: apikey: <key>  (secret 'ttsfree' me)
#   body: {"text", "voiceService", "voiceID", "voiceSpeed", "voicePitch"}
#   response: {"status": "success", "audioData": "<base64 mp3>"}
# Max 500 chars/request isliye lambi script chunks me jaati hai.
# Voice IDs: https://ttsfree.com/api/v1/voice (hi-IN = Madhur male,
#   hi-IN2 = Swara female - config me badal sakte ho)
# ----------------------------------------------------------------------------

def _ttsfree(text, cfg, out_path):
    """TTSFree.com API se Hindi voice banao."""
    import base64

    api_key = _ttsfree_key()
    voice_service = cfg.get("voice_service", "servicebin")
    voice_id = cfg.get("voice_id", "hi-IN")
    speed = str(cfg.get("voice_speed", "0"))
    pitch = str(cfg.get("voice_pitch", "0"))

    # API max 500 chars per request leta hai
    chunks = _split_script(text, max_chars=450)
    parts = []
    for i, chunk in enumerate(chunks):
        print(f"  [ttsfree] chunk {i + 1}/{len(chunks)} generate ho raha hai...")
        last_err = None
        for attempt in range(1, 4):   # network hiccup pe 3 attempts
            try:
                resp = requests.post(
                    "https://ttsfree.com/api/v1/tts",
                    headers={
                        "apikey": api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": chunk,
                        "voiceService": voice_service,
                        "voiceID": voice_id,
                        "voiceSpeed": speed,
                        "voicePitch": pitch,
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "success" or not data.get("audioData"):
                    raise RuntimeError(f"ttsfree response: {str(data)[:200]}")
                part = out_path.parent / f"ttsfree_part{i}.mp3"
                part.write_bytes(base64.b64decode(data["audioData"]))
                if part.stat().st_size < 1000:
                    raise RuntimeError("ttsfree audio bahut chhota aaya")
                parts.append(part)
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(5 * attempt)
        else:
            raise RuntimeError(f"ttsfree chunk {i + 1} fail (3 attempts): {last_err}")

    if len(parts) == 1:
        shutil.copy(parts[0], out_path)
        return out_path
    _concat_files(parts, out_path, reencode=True)
    return out_path


# ----------------------------------------------------------------------------
# IndicF5 - AI4Bharat ka near-human polyglot TTS (HuggingFace Space, FREE)
#
# Voice-clone model hai: text + reference audio + uska transcript.
# Model polyglot hai - kisi bhi bhasha ke reference se Hindi bol sakta hai.
# Voices niche INDICF5_VOICES me hain (AI4Bharat ke official prompts).
# ----------------------------------------------------------------------------

INDICF5_VOICES = {
    # energetic female voice - Hindi synth ke liye space ka apna demo isi se
    # hota hai, best default
    "punjabi_female_happy": {
        "url": "https://github.com/AI4Bharat/IndicF5/raw/refs/heads/main/prompts/PAN_F_HAPPY_00002.wav",
        "ref_text": "\u0a07\u0a71\u0a15 \u0a17\u0a3c\u0a30\u0a3e\u0a39\u0a15 \u0a28\u0a47 \u0a38\u0a3e\u0a21\u0a40 \u0a2c\u0a47\u0a2e\u0a3f\u0a38\u0a3e\u0a32 \u0a38\u0a47\u0a35\u0a3e \u0a2c\u0a3e\u0a30\u0a47 \u0a26\u0a3f\u0a32\u0a4b\u0a02\u0a17\u0a35\u0a3e\u0a39\u0a40 \u0a26\u0a3f\u0a71\u0a24\u0a40 \u0a1c\u0a3f\u0a38 \u0a28\u0a3e\u0a32 \u0a38\u0a3e\u0a28\u0a42\u0a70 \u0a05\u0a28\u0a70\u0a26 \u0a2e\u0a39\u0a3f\u0a38\u0a42\u0a38 \u0a39\u0a4b\u0a07\u0a06\u0964",
    },
    # female, happy
    "tamil_female_happy": {
        "url": "https://github.com/AI4Bharat/IndicF5/raw/refs/heads/main/prompts/TAM_F_HAPPY_00001.wav",
        "ref_text": "\u0ba8\u0bbe\u0ba9\u0bcd \u0ba8\u0bc6\u0ba9\u0b9a\u0bcd\u0b9a \u0bae\u0bbe\u0ba4\u0bbf\u0bb0\u0bbf\u0baf\u0bc7 \u0b85\u0bae\u0bc7\u0b9a\u0bbe\u0ba9\u0bcd\u0bb2 \u0baa\u0bc6\u0bb0\u0bbf\u0baf \u0ba4\u0bb3\u0bcd\u0bb3\u0bc1\u0baa\u0b9f\u0bbf \u0bb5\u0ba8\u0bcd\u0ba4\u0bbf\u0bb0\u0bc1\u0b95\u0bcd\u0b95\u0bc1. \u0b95\u0bae\u0bcd\u0bae\u0bbf \u0b95\u0bbe\u0b9a\u0bc1\u0b95\u0bcd\u0b95\u0bc7 \u0b85\u0ba8\u0bcd\u0ba4\u0baa\u0bcd \u0baa\u0bc1\u0ba4\u0bc1 \u0b9a\u0bc7\u0bae\u0bcd\u0b9a\u0b99\u0bcd \u0bae\u0bbe\u0b9f\u0ba9\u0bcd \u0bb5\u0bbe\u0b99\u0bcd\u0b95\u0bbf\u0b9f\u0bb2\u0bbe\u0bae\u0bcd.",
    },
    # female, calm (wiki-style narration)
    "marathi_female_wiki": {
        "url": "https://github.com/AI4Bharat/IndicF5/raw/refs/heads/main/prompts/MAR_F_WIKI_00001.wav",
        "ref_text": "\u0926\u093f\u0917\u0902\u0924\u0930\u093e\u0935\u094d\u0926\u093e\u0930\u0947 \u0905\u0902\u0924\u0930\u0933 \u0915\u0915\u094d\u0937\u0947\u0924\u0932\u093e \u0915\u091a\u0930\u093e \u091a\u093f\u0928\u094d\u0939\u093f\u0924 \u0915\u0930\u0923\u094d\u092f\u093e\u0938\u093e\u0920\u0940 \u092a\u094d\u0930\u092f\u0924\u094d\u0928 \u0915\u0947\u0932\u0947 \u091c\u093e\u0924 \u0906\u0939\u0947.",
    },
    # male voice
    "marathi_male_wiki": {
        "url": "https://github.com/AI4Bharat/IndicF5/raw/refs/heads/main/prompts/MAR_M_WIKI_00001.wav",
        "ref_text": "\u092f\u093e \u092a\u094d\u0930\u0925\u093e\u0932\u093e \u090f\u0915\u094b\u0923\u0940\u0938\u0936\u0947 \u092a\u0902\u091a\u093e\u0924\u0930 \u0908\u0938\u0935\u0940 \u092a\u093e\u0938\u0cbe\u0ca8 \u092d\u093e\u0930\u0924\u0940\u092f \u0926\u0902\u0921 \u0938\u0902\u0939\u093f\u0924\u093e\u091a\u0940 \u0927\u093e\u0930\u093e \u091a\u093e\u0930\u0936\u0947 \u0905\u0920\u094d\u0920\u093e\u0935\u0940\u0938 \u0906\u0923\u093f \u091a\u093e\u0930\u0936\u0947 \u090f\u0915\u094b\u0923\u0924\u0940\u0938\u091a\u094d\u092f\u093e \u0905\u0928\u094d\u0924\u0930\u094d\u0917\u0924 \u0928\u093f\u0937\u0947\u0927 \u0915\u0947\u0932\u093e.",
    },
    # female, happy
    "kannada_female_happy": {
        "url": "https://github.com/AI4Bharat/IndicF5/raw/refs/heads/main/prompts/KAN_F_HAPPY_00001.wav",
        "ref_text": "\u0ca8\u0cae\u0ccd \u0cab\u0ccd\u0cb0\u0cbf\u0c9c\u0ccd\u0c9c\u0cb2\u0ccd\u0cb2\u0cbf \u0c95\u0cc2\u0cb2\u0cbf\u0c82\u0c97\u0ccd \u0cb8\u0cae\u0cb8\u0ccd\u0caf\u0cc6 \u0c86\u0c97\u0cbf \u0ca8\u0cbe\u0ca8\u0ccd \u0cad\u0cbe\u0cb3 \u0ca6\u0cbf\u0ca8\u0ca6\u0cbf\u0c82\u0ca6 \u0c92\u0ca6\u0ccd\u0ca6\u0cbe\u0ca1\u0ccd\u0ca4\u0cbf\u0ca6\u0ccd\u0ca6\u0cc6, \u0c86\u0ca6\u0ccd\u0cb0\u0cc6 \u0c85\u0ca6\u0ccd\u0ca8\u0cc0\u0c97 \u0cae\u0cc6\u0c95\u0cbe\u0ca8\u0cbf\u0c95\u0ccd \u0c86\u0c97\u0cbf\u0cb0\u0ccb \u0ca8\u0cbf\u0cae\u0ccd \u0cb8\u0cb9\u0cbe\u0caf\u0ccd\u0ca6\u0cbf\u0c82\u0ca6 \u0cac\u0c97\u0cc6\u0cb9\u0cb0\u0cbf\u0cb8\u0ccd\u0c95\u0ccb\u0cac\u0ccb\u0ca6\u0cc1 \u0c85\u0c82\u0ca4\u0cbe\u0c97\u0cbf \u0ca8\u0cbf\u0cb0\u0cbe\u0cb3 \u0c86\u0caf\u0ccd\u0ca4\u0cc1 \u0ca8\u0c82\u0c97\u0cc6.",
    },
}


def _split_script(text, max_chars=240):
    """Lambi script ko sentence boundaries pe chunks me todo."""
    sentences = re.split(r"(?<=[.\u0964!?])\s+", text.strip())
    chunks, cur = [], ""
    for s in sentences:
        if not s:
            continue
        if len(cur) + len(s) + 1 <= max_chars:
            cur = f"{cur} {s}".strip()
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks or [text]


# IndicF5 spaces ki chain - pehla fail ho to agla try hota hai
# (official space kabhi kabhi maintenance/break ho jata hai,
#  mirrors usi code ke copies hain)
INDICF5_SPACES = [
    "ai4bharat/IndicF5",
    "YashMachineLearning/IndicF5-1",
    "joshiyash666/IndicF5",
]


def _write_wav(path, sample_rate, audio_np):
    """numpy audio ko WAV file me likho (mono, 16-bit PCM)."""
    import wave

    import numpy as np

    audio = np.asarray(audio_np)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if audio.dtype != np.int16:
        audio = np.clip(audio.astype(np.float64), -1.0, 1.0)
        audio = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(audio.tobytes())


def _indicf5(text, cfg, out_path):
    """AI4Bharat IndicF5 via HuggingFace Space (FREE, near-human Hindi).

    - Spaces ki chain try hoti hai (official -> mirrors)
    - Space pe queue lag sakti hai (30-60s video ke liye ~1-3 min)
    - HF_TOKEN (agar set ho) se rate-limit better rehti hai
    - Lambi script automatic sentence-level chunks me jaati hai
    """
    from gradio_client import Client

    voice_name = cfg.get("voice", "punjabi_female_happy")
    voice = INDICF5_VOICES.get(voice_name) or INDICF5_VOICES["punjabi_female_happy"]

    # reference audio ek baar download karke cache kar lo
    ref_path = out_path.parent / "indicf5_ref.wav"
    if not ref_path.exists():
        r = requests.get(voice["url"], timeout=120)
        r.raise_for_status()
        ref_path.write_bytes(r.content)

    chunks = _split_script(text)

    last_err = None
    for space in INDICF5_SPACES:
        try:
            client = Client(space, token=os.environ.get("HF_TOKEN") or None)

            parts = []
            for i, chunk in enumerate(chunks):
                print(f"  [IndicF5:{space}] chunk {i + 1}/{len(chunks)} generate ho raha hai...")
                result = client.predict(
                    text=chunk,
                    ref_audio=str(ref_path),
                    ref_text=voice["ref_text"],
                    api_name="/synthesize_speech",
                )
                sample_rate, audio = result[0], result[1]
                part = out_path.parent / f"indicf5_part{i}.wav"
                _write_wav(part, sample_rate, audio)
                parts.append(part)

            out_wav = out_path.with_suffix(".wav")
            if len(parts) == 1:
                shutil.copy(parts[0], out_wav)
                return out_wav
            _concat_files(parts, out_wav, reencode=False)
            return out_wav
        except Exception as e:  # noqa: BLE001 - agla space try karo
            last_err = e
            print(f"  WARNING: space '{space}' fail hua ({str(e)[:150]})")

    raise RuntimeError(f"IndicF5 ke saare spaces fail ho gaye: {last_err}")


def _parler(text, cfg, out_path):
    """AI4Bharat Indic Parler-TTS via HuggingFace Space (FREE).
    IndicF5 down hone par iska fallback hai - natural 'Aman' voice.
    """
    from gradio_client import Client

    space = cfg.get("space", "ai4bharat/indic-parler-tts")
    # voice description me hi speaker ka naam hota hai (Aman/Rohit/Divya/Rani)
    desc = cfg.get(
        "voice_desc",
        "Aman speaks in an expressive and energetic tone, at a slightly fast "
        "pace, in a very clear recording with no background noise.",
    )

    last_err = None
    for api_name in ("/generate_finetuned", "/generate_base"):
        try:
            client = Client(space, token=os.environ.get("HF_TOKEN") or None)
            result = client.predict(
                text=text, description=desc, api_name=api_name
            )
            path = result[0] if isinstance(result, (tuple, list)) else result
            if path and Path(path).exists() and Path(path).stat().st_size > 1000:
                shutil.copy(path, out_path)
                return out_path
        except Exception as e:  # noqa: BLE001 - finetuned -> base fallback
            last_err = e
            continue

    raise RuntimeError(f"Parler TTS (space) fail: {last_err}")


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
