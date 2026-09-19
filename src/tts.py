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

    IMPORTANT: sentence-ending punctuation (à¥¤ . ! ?) ko rakhna hai -
    natural TTS isi se intonation aur natural pauses banata hai.
    Sirf wahi cheezein hatao jo LAMBE silence gap banati hain.
    """
    # ellipses (..., ...) -> single full stop (ye sabse lamba gap banata hai)
    text = re.sub(r"\.{2,}", ".", text)
    text = text.replace("\u2026", ".")
    # dashes (-, â€“, â€”) jo beech me pause banate hain
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
            ("Gemini", lambda: _gemini_tts(text, tts_cfg.get("gemini", {}), out_path)),            ("TTSFree", lambda: _ttsfree(text, tts_cfg.get("ttsfree", {}), out_path)),
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
        except Exception as e:  # noqa: BLE001 - alla fallback
            last_err = e
            print(f"WARNING: {name} TTS fail hua ({str(e)[:200]})")
            print(f"         agla fallback try kar rahe hain...")
    raise RuntimeError(f"Saare TTS providers fail ho gaye: {last_err}")


# -----------------------------------------------------------------------------
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

    - GEMINI_API_KEY hi chahiye (repo me pehle se hai) - koi naya key nahi
    - lambi script automatic chunks me jaati hai, phir WAV concat
    - fail hone pe fallback chain (ttsfree/IndicF5/edge-tts) chalti hai
    """
    import base64

    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY env/secret set nahi hai.")

    model = cfg.get("model", "gemini-2.5-flash-preview-tts")
    voice = cfg.get("voice", "Puck")          # male: Puck/Charon/Fenrir/Orus
    style = cfg.get(
        "style",
        "Say in an energetic, engaging and clear YouTube shorts narrator "
        "tone, speaking natural Hindi:",
    )

    client = genai.Client(api_key=api_key)

    chunks = _split_script(text, max_chars=1500)
    parts = []
    for i, chunk in enumerate(chunks):
        print(f"  [GeminiTTS] chunk {i + 1}/{len(chunks)} generate ho raha hai...")
        resp = client.models.generate_content(
            model=model,
            contents=f"{style}\n\n{chunk}",
            config=types.GenerateContentConfig(
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
    """TTSFree.com API se Hindi voice banao."""ˆ[\Ü˜\ÙM‚ˆ\WÚÙ^HHİÙœ™YWÚÙ^J
Bˆ›ÚXÙWÜÙ\šXÙHHÙ™Ë™Ù]
›ÚXÙWÜÙ\šXÙH‹œÙ\šXÙXš[ˆŠBˆ›ÚXÙWÚYHÙ™Ë™Ù]
›ÚXÙWÚY‹šKRSˆŠBˆÜYYHİŠÙ™Ë™Ù]
›ÚXÙWÜÜYY‹ŒŠJBˆ]ÚHİŠÙ™Ë™Ù]
›ÚXÙWÜ]Ú‹ŒŠJB‚ˆÈTHX^LÚ\œÈ\ˆ™\]Y\İ]HZBˆÚ[šÜÈHÜÜ]ÜØÜš\
^X^ØÚ\œÏML
Bˆ\ÈH×Bˆ›ÜˆKÚ[šÈ[ˆ[[Y\˜]JÚ[šÜÊN‚ˆš[
ˆˆİÙœ™YWHÚ[šÈÚH
È_KŞÛ[ŠÚ[šÜÊ_HÙ[™\˜]HÈ˜ZHZK‹‹ˆŠBˆ\İÙ\œˆH›Û™Bˆ›Üˆ][\[ˆ˜[™ÙJK
NˆÈ™]ÛÜšÈXØİ\HÈ][\ÂˆN‚ˆ™\ÜH™\]Y\İËœÜİ
ˆšÎ‹ËİÙœ™YK˜ÛÛKØ\KİŒKİÈ‹ˆXY\œÏ^Âˆ˜\ZÙ^Hˆ\WÚÙ^KˆÛÛ[U\Hˆ˜\XØ][Û‹ÚœÛÛˆ‹ˆKˆœÛÛ^Âˆ^ˆÚ[šËˆ›ÚXÙTÙ\šXÙHˆ›ÚXÙWÜÙ\šXÙKˆ›ÚXÙRQˆ›ÚXÙWÚYˆ›ÚXÙTÜYYˆÜYYˆ›ÚXÙT]Úˆ]ÚˆKˆ[Y[İ]LLŒˆ
Bˆ™\Üœ˜Z\ÙWÙ›Ü—Üİ]\Ê
Bˆ]HH™\ÜšœÛÛŠ
BˆYˆ]K™Ù]
œİ]\ÈŠHOHœİXØÙ\ÜÈˆÜˆ›İ]K™Ù]
˜]Y[Ñ]HŠN‚ˆ˜Z\ÙH[[YQ\œ›ÜŠˆÙœ™YH™\ÜÛœÙNˆÜİŠ]JVÎŒŒ_HŠBˆ\Hİ]Ü]œ\™[ÈˆÙœ™YWÜ\Ú_K›\È‚ˆ\Üš]WØ]\Ê˜\ÙM˜XÛÙJ]VÈ˜]Y[Ñ]H—JJBˆYˆ\œİ]

KœİÜÚ^™HL‚ˆ˜Z\ÙH[[YQ\œ›ÜŠÙœ™YH]Y[È˜Z]ÚİHX^XHŠBˆ\Ë˜\[™
\
Bˆœ™XZÂˆ^Ù\^Ù\[Ûˆ\ÈNˆÈ›ÜXNˆ“LBˆ\İÙ\œˆHBˆ[YKœÛY\
H
ˆ][\
Bˆ[ÙN‚ˆ˜Z\ÙH[[YQ\œ›ÜŠˆÙœ™YHÚ[šÈÚH
È_H˜Z[
È][\ÊNˆÛ\İÙ\œŸHŠB‚ˆYˆ[Š\ÊHOHN‚ˆÚ][˜ÛÜJ\ÖÌKİ]Ü]
Bˆ™]\›ˆİ]Ü]ˆØÛÛ˜Ø]Ùš[\Ê\Ëİ]Ü]™Y[˜ÛÙOUYJBˆ™]\›ˆİ]Ü]‚‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKBˆÈ[™XÑHHRMš\˜]ØH™X\‹Z[X[ˆÛYÛİÈ
YÙÚ[™Ñ˜XÙHÜXÙK”‘QJBˆÂˆÈ›ÚXÙKXÛÛ™H[Ù[ZNˆ^
È™Y™\™[˜ÙH]Y[È
È\ÚØH˜[œØÜš\‚ˆÈ[Ù[ÛYÛİZHHÚ\ÚHšHš\ÚHÙH™Y™\™[˜ÙHÙH[™H›ÛØZİHZK‚ˆÈ›ÚXÙ\ÈšXÚHS‘PÑWÕ“ÒPÑTÈYHZ[ˆ
RMš\˜]ÙHÙ™šXÚX[›Û\ÊK‚ˆÈKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKB‚’S‘PÑWÕ“ÒPÑTÈHÂˆÈ[™\™Ù]XÈ™[X[H›ÚXÙHH[™HŞ[ÙH^YHÜXÙHØH\˜H[[È\ÚHÙBˆÈİHZK™\İY˜][ˆœ[š˜XšWÙ™[X[WÚ\HˆÂˆ\›ˆšÎ‹ËÙÚ]X‹˜ÛÛKĞRMš\˜]Ò[™XÑKÜ˜]ËÜ™YœËÚXYËÛXZ[‹Ü›Û\ËÔS—Ñ—ÒTWÌ‹Ø]ˆ‹ˆœ™Y—İ^ˆ—LL×LMÌWLLMHLLM×LLØ×LLÌLLÙWLLÎWLLMHLLLMÈLLÎLLÙWLLŒWLMLL˜×LM×LL™WLLÙ—LLÎLLÙWLLÌˆLLÎLM×LLÍWLLÙHLL˜×LLÙWLLÌLMÈLL—LLÙ—LLÌ—LM—LL—LLM×LLÍWLLÙWLLÎWLMLL—LLÙ—LMÌWLLLMLLX×LLÙ—LLÎLLLLÙWLLÌˆLLÎLLÙWLLLM—LMÌLLWLLLMÌLLˆLL™WLLÎWLLÙ—LLÎLM—LLÎLLÎWLM—LL×LL—LM‹ˆKˆÈ™[X[K\Bˆ[Z[Ù™[X[WÚ\HˆÂˆ\›ˆšÎ‹ËÙÚ]X‹˜ÛÛKĞRMš\˜]Ò[™XÑKÜ˜]ËÜ™YœËÚXYËÛXZ[‹Ü›Û\ËÕSWÑ—ÒTWÌKØ]ˆ‹ˆœ™Y—İ^ˆ—L˜NL˜™WL˜NWL˜ÙL˜NL˜Í—L˜NWLXWL˜ÙLXHL˜YWL˜™WL˜ML˜™—L˜ŒL˜™—L˜Y—L˜ÍÈLWL˜YWL˜Í×LXWL˜™WL˜NWL˜ÙL˜ŒˆL˜XWL˜Í—L˜ŒL˜™—L˜YˆL˜ML˜Œ×L˜ÙL˜Œ×L˜ÌWL˜XWLY—L˜™ˆL˜WL˜NL˜ÙL˜ML˜™—L˜ŒL˜ÌWLMWL˜ÙLMWL˜ÌKˆLMWL˜YWL˜ÙL˜YWL˜™ˆLMWL˜™WLXWL˜ÌWLMWL˜ÙLMWL˜ÍÈLWL˜NL˜ÙL˜ML˜XWL˜ÙL˜XWL˜ÌWL˜ML˜ÌHLXWL˜Í×L˜YWL˜ÙLNWL˜ÙL˜YWL˜™WLY—L˜NWL˜ÙL˜WL˜™WLNWL˜ÙLMWL˜™—LY—L˜Œ—L˜™WL˜YWL˜Ùˆ‹ˆKˆÈ™[X[KØ[H
ÚZÚK\İ[H˜\œ˜][ÛŠBˆ›X\˜]WÙ™[X[WİÚZÚHˆÂˆ\›ˆšÎ‹ËÙÚ]X‹˜ÛÛKĞRMš\˜]Ò[™XÑKÜ˜]ËÜ™YœËÚXYËÛXZ[‹Ü›Û\ËÓPT—Ñ—ÕÒRÒWÌKØ]ˆ‹ˆœ™Y—İ^ˆ—LL—LLÙ—LLM×LL—LLLLÌLLÙWLLÍWLMLL—LLÙWLLÌLMÈLLWLL—LLLLÌLLÌÈLLMWLLMWLMLLÍ×LM×LLLLÌ—LLÙHLLMWLLXWLLÌLLÙHLLXWLLÙ—LLLMLLÎWLLÙ—LLLLMWLLÌLLŒ×LMLL™—LLÙWLLÎLLÙWLLŒLMLL˜WLMLLÌLL™—LLLMLLLLMWLM×LLÌ—LMÈLLX×LLÙWLLLL—LLÎWLMËˆ‹ˆKˆÈX[H›ÚXÙBˆ›X\˜]WÛX[WİÚZÚHˆÂˆ\›ˆšÎ‹ËÙÚ]X‹˜ÛÛKĞRMš\˜]Ò[™XÑKÜ˜]ËÜ™YœËÚXYËÛXZ[‹Ü›Û\ËÓPT—ÓWÕÒRÒWÌKØ]ˆ‹ˆœ™Y—İ^ˆ—LL™—LLÙHLL˜WLMLLÌLLWLLÙWLLÌ—LLÙHLL—LLMWLM—LLŒ×LMLLÎLLÍ—LMÈLL˜WLL—LLXWLLÙWLLLLÌLLLLÎLLÍWLMLL˜WLLÙWLLÎLM—LLLL™LLÙWLLÌLLLMLL™ˆLL—LL—LLŒHLLÎLL—LLÎWLLÙ—LLLLÙWLLXWLMLL×LLÙWLLÌLLÙHLLXWLLÙWLLÌLLÍ—LMÈLLWLLŒLMLLŒLLÙWLLÍWLMLLÎLL—LLŒ×LLÙˆLLXWLLÙWLLÌLLÍ—LMÈLL—LLMWLM—LLŒ×LLLMLLÎLLXWLMLL™—LLÙHLLWLLLMLLLLÌLMLLM×LLLLLLÙ—LLÍ×LM×LLÈLLMWLM×LLÌ—LLÙKˆ‹ˆKˆÈ™[X[K\BˆšØ[›˜YWÙ™[X[WÚ\HˆÂˆ\›ˆšÎ‹ËÙÚ]X‹˜ÛÛKĞRMš\˜]Ò[™XÑKÜ˜]ËÜ™YœËÚXYËÛXZ[‹Ü›Û\ËÒĞS—Ñ—ÒTWÌKØ]ˆ‹ˆœ™Y—İ^ˆ—LØNLØYWLØÙLØX—LØÙLØŒLØ™—LÎX×LØÙLÎX×LØŒ—LØÙLØŒ—LØ™ˆLÎMWLØÌ—LØŒ—LØ™—LÎ—LÎM×LØÙLØLØYWLØLØÙLØY—LØÍˆLÎ—LÎM×LØ™ˆLØNLØ™WLØNLØÙLØYLØ™WLØŒÈLØM—LØ™—LØNLØM—LØ™—LÎ—LØMˆLÎL—LØM—LØÙLØM—LØ™WLØLWLØÙLØMLØ™—LØM—LØÙLØM—LØÍ‹LÎ—LØM—LØÙLØŒLØÍˆLÎWLØM—LØÙLØNLØÌLÎMÈLØYWLØÍ—LÎMWLØ™WLØY—LØ™—LÎMWLØÙLÎ—LÎM×LØ™—LØŒLØØˆLØNLØ™—LØYWLØÙLØLØWLØ™WLØY—LØÙLØM—LØ™—LÎ—LØMˆLØX×LÎM×LØÍ—LØWLØŒLØ™—LØLØÙLÎMWLØØ—LØX×LØØ—LØM—LØÌHLÎWLÎ—LØMLØ™WLÎM×LØ™ˆLØNLØ™—LØŒLØ™WLØŒÈLÎ—LØY—LØÙLØMLØÌHLØNLÎ—LÎM×LØÍ‹ˆ‹ˆKŸB‚‚™YˆÜÜ]ÜØÜš\
^X^ØÚ\œÏL
N‚ˆˆˆ“[XšHØÜš\ÛÈÙ[[˜ÙH›İ[™\šY\ÈHÚ[šÜÈYHÙËˆˆˆ‚ˆÙ[[˜Ù\ÈH™KœÜ]
ˆŠÏVË—LMO×JWÊÈ‹^œİš\

JBˆÚ[šÜËİ\ˆH×Kˆ‚ˆ›ÜˆÈ[ˆÙ[[˜Ù\Î‚ˆYˆ›İÎ‚ˆÛÛ[YBˆYˆ[Šİ\ŠH
È[ŠÊH
ÈHHX^ØÚ\œÎ‚ˆİ\ˆHˆØİ\ŸHÜßH‹œİš\

Bˆ[ÙN‚ˆYˆİ\‚ˆÚ[šÜË˜\[™
İ\ŠBˆİ\ˆHÂˆYˆİ\‚ˆÚ[šÜË˜\[™
İ\ŠBˆ™]\›ˆÚ[šÜÈÜˆİ^B‚‚ˆÈ[™XÑHÜXÙ\ÈÚHÚZ[ˆHZH˜Z[ÈÈYÛHHİHZBˆÈ
Ù™šXÚX[ÜXÙHØXšHØXšHXZ[[˜[˜ÙKØœ™XZÈÈ˜]HZKˆÈZ\œ›ÜœÈ\ÚHÛÙHÙHÛÜY\ÈZ[ŠB’S‘PÑWÔÔPÑTÈHÂˆ˜ZMš\˜]Ò[™XÑH‹ˆ–X\ÚXXÚ[™SX\›š[™ËÒ[™XÑKLH‹ˆš›ÜÚ^X\Ú‹Ò[™XÑH‹—B‚‚™YˆİÜš]WİØ]Š]Ø[\WÜ˜]K]Y[×Ûœ
N‚ˆˆˆ›[\H]Y[ÈÛÈĞUˆš[HYHZÚÈ
[Û›ËM‹Xš]ÓJKˆˆˆ‚ˆ[\ÜØ]™B‚ˆ[\Ü[\H\Èœ‚ˆ]Y[ÈHœ˜\Ø\œ˜^J]Y[×Ûœ
BˆYˆ]Y[Ë›™[HˆN‚ˆ]Y[ÈH]Y[Ë›YX[Š^\ÏLJBˆYˆ]Y[Ë™\HOHœš[M‚ˆ]Y[ÈHœ˜Û\
]Y[Ë˜\İ\Jœ™›Ø]
KLKŒKŒ
Bˆ]Y[ÈH
]Y[È
ˆÌÍÊK˜\İ\Jœš[MŠBˆÚ]Ø]™K›Ü[ŠİŠ]
KØˆŠH\ÈÎ‚ˆËœÙ]˜Ú[›™[ÊJBˆËœÙ]Ø[\ÚY
ŠBˆËœÙ]œ˜[Y\˜]J[
Ø[\WÜ˜]JJBˆËÜš]Yœ˜[Y\Ê]Y[ËØ]\Ê
JB‚‚™YˆÚ[™XÙJ^Ù™Ëİ]Ü]
N‚ˆˆˆRMš\˜][™XÑHšXHYÙÚ[™Ñ˜XÙHÜXÙH
”‘QK™X\‹Z[X[ˆ[™JK‚‚ˆHÜXÙ\ÈÚHÚZ[ˆHİHZH
Ù™šXÚX[OˆZ\œ›ÜœÊBˆHÜXÙHH]Y]YHYÈØZİHZH
ÌMŒÈšY[ÈÙH^YHŒKLÈZ[ŠBˆH—ÕÒÑSˆ
YØ\ˆÙ]ÊHÙH˜]K[[Z]™]\ˆ™ZHZBˆH[XšHØÜš\]]ÛX]XÈÙ[[˜ÙK[]™[Ú[šÜÈYH˜X]HZBˆˆˆ‚ˆœ›ÛHÜ˜Y[×ØÛY[[\ÜÛY[‚ˆ›ÚXÙWÛ˜[YHHÙ™Ë™Ù]
›ÚXÙH‹œ[š˜XšWÙ™[X[WÚ\HŠBˆ›ÚXÙHHS‘PÑWÕ“ÒPÑTË™Ù]
›ÚXÙWÛ˜[YJHÜˆS‘PÑWÕ“ÒPÑTÖÈœ[š˜XšWÙ™[X[WÚ\H—B‚ˆÈ™Y™\™[˜ÙH]Y[ÈZÈ˜X\ˆİÛ›ØYØ\šÙHØXÚHØ\ˆÂˆ™Y—Ü]Hİ]Ü]œ\™[Èš[™XÙWÜ™Y‹Ø]ˆ‚ˆYˆ›İ™Y—Ü]™^\İÊ
N‚ˆˆH™\]Y\İË™Ù]
›ÚXÙVÈ\›—K[Y[İ]LLŒ
Bˆ‹œ˜Z\ÙWÙ›Ü—Üİ]\Ê
Bˆ™Y—Ü]Üš]WØ]\Ê‹˜ÛÛ[
B‚ˆÚ[šÜÈHÜÜ]ÜØÜš\
^
B‚ˆ\İÙ\œˆH›Û™Bˆ›ÜˆÜXÙH[ˆS‘PÑWÔÔPÑTÎ‚ˆN‚ˆÛY[HÛY[
ÜXÙKÚÙ[[ÜË™[š\›Û‹™Ù]
’—ÕÒÑSˆŠHÜˆ›Û™JB‚ˆ\ÈH×Bˆ›ÜˆKÚ[šÈ[ˆ[[Y\˜]JÚ[šÜÊN‚ˆš[
ˆˆÒ[™XÑNÜÜXÙ_WHÚ[šÈÚH
È_KŞÛ[ŠÚ[šÜÊ_HÙ[™\˜]HÈ˜ZHZK‹‹ˆŠBˆ™\İ[HÛY[œ™YXİ
ˆ^XÚ[šËˆ™Y—Ø]Y[Ï\İŠ™Y—Ü]
Kˆ™Y—İ^]›ÚXÙVÈœ™Y—İ^—Kˆ\WÛ˜[YOH‹ÜŞ[\Ú^™WÜÜYXÚ‹ˆ
BˆØ[\WÜ˜]K]Y[ÈH™\İ[ÌK™\İ[ÌWBˆ\Hİ]Ü]œ\™[Èˆš[™XÙWÜ\Ú_KØ]ˆ‚ˆİÜš]WİØ]Š\Ø[\WÜ˜]K]Y[ÊBˆ\Ë˜\[™
\
B‚ˆİ]İØ]ˆHİ]Ü]Ú]ÜİY™š^
‹Ø]ˆŠBˆYˆ[Š\ÊHOHN‚ˆÚ][˜ÛÜJ\ÖÌKİ]İØ]ŠBˆ™]\›ˆİ]İØ]‚ˆØÛÛ˜Ø]Ùš[\Ê\Ëİ]İØ]‹™Y[˜ÛÙOQ˜[ÙJBˆ™]\›ˆİ]İØ]‚ˆ^Ù\^Ù\[Ûˆ\ÈNˆÈ›ÜXNˆ“LHHYÛHÜXÙHHØ\›Âˆ\İÙ\œˆHBˆš[
ˆˆĞT“’S‘ÎˆÜXÙH	ŞÜÜXÙ_IÈ˜Z[XH
ÜİŠJVÎŒML_JHŠB‚ˆ˜Z\ÙH[[YQ\œ›ÜŠˆ’[™XÑHÙHØX\™HÜXÙ\È˜Z[ÈØ^YNˆÛ\İÙ\œŸHŠB‚‚™YˆÜ\›\Š^Ù™Ëİ]Ü]
N‚ˆˆˆRMš\˜][™XÈ\›\‹UÈšXHYÙÚ[™Ñ˜XÙHÜXÙH
”‘QJK‚ˆ[™XÑHİÛˆÛ™H\ˆ\ÚØH˜[˜XÚÈZHH˜]\˜[	Ğ[X[‰È›ÚXÙK‚ˆˆˆ‚ˆœ›ÛHÜ˜Y[×ØÛY[[\ÜÛY[‚ˆÜXÙHHÙ™Ë™Ù]
œÜXÙH‹˜ZMš\˜]Ú[™XË\\›\‹]ÈŠBˆÈ›ÚXÙH\ØÜš\[ÛˆYHHÜXZÙ\ˆØH˜[[HİHZH
[X[‹Ô›Ú]Ñ]XKÔ˜[šJBˆ\ØÈHÙ™Ë™Ù]
ˆ›ÚXÙWÙ\ØÈ‹ˆ[X[ˆÜXZÜÈ[ˆ[ˆ^™\ÜÚ]™H[™[™\™Ù]XÈÛ™K]HÛYÚH˜\İ‚ˆœXÙK[ˆH™\HÛX\ˆ™XÛÜ™[™ÈÚ]›È˜XÚÙÜ›İ[™›Ú\ÙKˆ‹ˆ
B‚ˆ\İÙ\œˆH›Û™Bˆ›Üˆ\WÛ˜[YH[ˆ
‹ÙÙ[™\˜]WÙš[™][™Y‹‹ÙÙ[™\˜]WØ˜\ÙHŠN‚ˆN‚ˆÛY[HÛY[
ÜXÙKÚÙ[[ÜË™[š\›Û‹™Ù]
’—ÕÒÑSˆŠHÜˆ›Û™JBˆ™\İ[HÛY[œ™YXİ
ˆ^]^\ØÜš\[ÛY\ØË\WÛ˜[YOX\WÛ˜[YBˆ
Bˆ]H™\İ[ÌHYˆ\Ú[œİ[˜ÙJ™\İ[
\K\İ
JH[ÙH™\İ[ˆYˆ][™]
]
K™^\İÊ
H[™]
]
Kœİ]

KœİÜÚ^™HˆL‚ˆÚ][˜ÛÜJ]İ]Ü]
Bˆ™]\›ˆİ]Ü]ˆ^Ù\^Ù\[Ûˆ\ÈNˆÈ›ÜXNˆ“LHHš[™][™YOˆ˜\ÙH˜[˜XÚÂˆ\İÙ\œˆHBˆÛÛ[YB‚ˆ˜Z\ÙH[[YQ\œ›ÜŠˆ”\›\ˆÈ
ÜXÙJH˜Z[ˆÛ\İÙ\œŸHŠB‚‚˜\Ş[˜ÈYˆÙYÙWİ×Ø\Ş[˜Ê^Ù™Ëİ]Ü]
N‚ˆ[\ÜYÙWİÂ‚ˆÈ“ÒPÑH[ˆ
ÛÜšÙ›İÈÙHX]HZJHÛÛ™šYÈÛÈİ™\œšYHØ\HZBˆ›ÚXÙHHÜË™[š\›Û‹™Ù]
•“ÒPÑHŠHÜˆÙ™Ë™Ù]
›ÚXÙH‹šKRS‹SXY\“™]\˜[ŠBˆ˜]HHÙ™Ë™Ù]
œ˜]H‹ŠÌL‰HŠBˆÛÛ[][šXØ]HHYÙWİËÛÛ[][šXØ]J^›ÚXÙK˜]O\˜]JBˆ]ØZ]ÛÛ[][šXØ]KœØ]™JİŠİ]Ü]
JB‚‚™YˆÙYÙWİÊ^Ù™Ëİ]Ü]
N‚ˆ\Ş[˜Ú[Ëœ[ŠÙYÙWİ×Ø\Ş[˜Ê^Ù™Ëİ]Ü]
JBˆ™]\›ˆİ]Ü]‚‚™YˆÙÙ]Ø\WÚÙ^J
N‚ˆÙ^HHÜË™[š\›Û‹™Ù]
•×ĞTWÒÑVHŠBˆYˆ›İÙ^N‚ˆ˜Z\ÙH[[YQ\œ›ÜŠ•×ĞTWÒÑVH[‹ÜÙXÜ™]Ù]˜ZHZKˆŠBˆ™]\›ˆÙ^B‚‚™YˆØİ\İÛWÚ
^Ù™Ëİ]Ü]
N‚ˆˆˆ‘Ù[™\šXÈ[\]HH\˜HÛÚHšHÈTHXZ[ˆYÈØ\›È
ÛÙHÚ[™ÙH™\›ÊKˆˆˆ‚ˆXY\œÈHÈÛÛ[U\Hˆ˜\XØ][Û‹ÚœÛÛˆŸBˆ]]HÙ™Ë™Ù]
˜]]ÚXY\ˆ‹ˆŠBˆYˆ]]‚ˆXY\œÖÈ]]Üš^˜][Ûˆ—HH]]œ™\XÙJŞÕ×ĞTWÒÑV__H‹ÙÙ]Ø\WÚÙ^J
JB‚ˆ›ÙHHÛÜK™Y\ÛÜJÙ™Ë™Ù]
˜›ÙH‹ßJJBˆ›ÜˆËˆ[ˆ›ÙKš][\Ê
N‚ˆYˆ\Ú[œİ[˜ÙJ‹İŠN‚ˆ›ÙVÚ×HH‹œ™\XÙJİ^H‹^
B‚ˆ™\ÜH™\]Y\İËœÜİ
Ù™ÖÈ™[™Ú[—KXY\œÏZXY\œËœÛÛX›ÙK[Y[İ]LÌ
Bˆ™\Üœ˜Z\ÙWÙ›Ü—Üİ]\Ê
B‚ˆ™\ÜÛœÙWÜÜXÈHÙ™Ë™Ù]
œ™\ÜÛœÙH‹˜š[˜\HŠBˆYˆ™\ÜÛœÙWÜÜXÈOH˜š[˜\H‚ˆİ]Ü]Üš]WØ]\Ê™\Ü˜ÛÛ[
Bˆ[Yˆ™\ÜÛœÙWÜÜXËœİ\İÚ]
˜˜\ÙMÚœÛÛˆŠN‚ˆ]HH™\ÜšœÛÛŠ
Bˆ›Üˆ\[ˆ™\ÜÛœÙWÜÜXËœÜ]
ˆ‹JVÌWKœÜ]
‹ˆŠN‚ˆ]HH]VÜ\BˆYˆ\Ú[œİ[˜ÙJ]KİŠN‚ˆ]HH×Ú[\Ü×Ê˜˜\ÙMŠK˜XÛÙJ]JBˆİ]Ü]Üš]WØ]\Ê]JBˆ[ÙN‚ˆ˜Z\ÙH˜[YQ\œ›ÜŠˆœ™\ÜÛœÙHÜXÈØ[XZš˜ZHX^XNˆÜ™\ÜÛœÙWÜÜXÈ\ŸHŠBˆ™]\›ˆİ]Ü]‚‚™YˆÜØ\˜[J^Ù™Ëİ]Ü]
N‚ˆˆˆ”Ø\˜[HRHÈ^[\H[\[Y[][Û‹ˆˆˆ‚ˆ[\Ü˜\ÙM‚ˆ™\ÜH™\]Y\İËœÜİ
ˆšÎ‹ËØ\KœØ\˜[K˜ZKİ^]Ë\ÜYXÚ‹ˆXY\œÏ^È˜\K\İXœØÜš\[Û‹ZÙ^HˆÙÙ]Ø\WÚÙ^J
_KˆœÛÛ^Âˆ^ˆ^ˆœÜXZÙ\ˆˆÙ™Ë™Ù]
œÜXZÙ\ˆ‹›YY\˜HŠKˆ›[Ù[ˆÙ™Ë™Ù]
›[Ù[‹˜[[]ŒˆŠKˆœÜYXÚÜ˜]HˆÙ™Ë™Ù]
œÜYXÚÜ˜]H‹KŒMJKˆKˆ[Y[İ]LÌˆ
Bˆ™\Üœ˜Z\ÙWÙ›Ü—Üİ]\Ê
Bˆ]Y[×ØH™\ÜšœÛÛŠ
VÈ˜]Y[ÜÈ—VÌBˆİ]Ü]Üš]WØ]\Ê˜\ÙM˜XÛÙJ]Y[×Ø
JBˆ™]\›ˆİ]Ü]