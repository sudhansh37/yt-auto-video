"""
ffmpeg editor v5 - Hindi Short banata hai:

  1. Video ko 3:4 band me CROP (1080x1440) - TOP se anchor, BOTTOM crop hota
     hai (Zack D. Films ka English caption bottom pe hota hai - wahi kat jata
     hai, upar ka content safe rehta hai)
  2. Upar-neeche WHITE canvas (Instagram-style look)
  3. Zoom/pan effect (zoom_amount se control)
  4. Color grade: saturation + contrast + brightness
  5. Sharpness (unsharp)
  6. Video thodi tez + voice loudness normalize
  7. Slow lo-fi background music (ffmpeg synth - copyright free)
  8. Original audio HATA ke sirf Hindi TTS voice
  9. TIME-SYNCED HINGLISH CAPTIONS - jo script Gemini likhta hai (jo voice
     bolti hai) wahi text video pe bottom me white-on-black dikhta hai.
     Captions HINGLISH (Roman letters) me hoti hain - Devanagari text aaye
     to Devanagari font automatically use hota hai.

Captions ka style: neeche wali strip me white text + halka kaala box
(subtitle style). Hinglish ke liye DejaVu Sans Bold, Devanagari ke liye
Noto Sans Devanagari Bold (CI pe fonts-noto-core package se aata hai).
Har chunk apne time-window me dikhta hai.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

TARGET_W, TARGET_H, FPS = 1080, 1920, 30
BAND_W, BAND_H = 1080, 1440   # video band (3:4 crop) - iske upar/neeche white
BIG_W, BIG_H = 2160, 2880     # zoom se pehle 2x upscale (quality ke liye)

# caption settings
CAPTION_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
]

# HINGLISH (Roman letters) captions ke liye Latin fonts
LATIN_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _ffmpeg_exe():
    """System ffmpeg prefer karo, warna imageio-ffmpeg ka static binary."""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


FFMPEG = _ffmpeg_exe()


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg fail hua:\n{result.stderr[-2000:]}")
    return result


def _parse_duration_from_stderr(stderr):
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not m:
        raise RuntimeError("Duration parse nahi hui (ffprobe bhi nahi mila).")
    h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mnt * 60 + s


def get_duration(path):
    """ffprobe se duration; ffprobe na ho to ffmpeg -i se parse karo."""
    if shutil.which("ffprobe"):
        out = _run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ])
        return float(json.loads(out.stdout)["format"]["duration"])
    # fallback: ffmpeg ka stderr parse karo
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return _parse_duration_from_stderr(result.stderr)


def _has_devanagari(text):
    """Text me Devanagari letters hain? (Hinglish -> False, Hindi -> True)"""
    return any("\u0900" <= ch <= "\u097F" for ch in text)


def _find_caption_font(caption_text=""):
    """Caption text ke hisaab se font dhoondo.

    HINGLISH (Roman letters) -> DejaVu/Noto Sans Bold (Latin font)
    Devanagari             -> Noto Sans Devanagari Bold
    """
    deva = _has_devanagari(caption_text)
    candidates = CAPTION_FONT_CANDIDATES if deva else LATIN_FONT_CANDIDATES
    for f in candidates:
        if Path(f).exists():
            return f
    # glob fallback
    import glob
    if deva:
        pats = ("/usr/share/fonts/**/NotoSansDevanagari*.ttf",
                "/usr/share/fonts/**/*Devanagari*.ttf")
    else:
        pats = ("/usr/share/fonts/**/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/**/NotoSans-Bold.ttf",
                "/usr/share/fonts/**/*Sans*Bold*.ttf",
                "/usr/share/fonts/**/DejaVu*.ttf")
    for pat in pats:
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[0]
    return candidates[0]


def _caption_chunks(script, max_words=4, max_chars=26):
    """Script ko chhote caption chunks me todo (4 shabd / 26 chars tak)."""
    # Hinglish (Latin) me letters lambe hote hain - thoda wide chunk chalega
    if not _has_devanagari(script):
        max_chars = 32
    words = [w for w in script.split() if w.strip()]
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or len(" ".join(cur)) >= max_chars:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        chunks.append(" ".join(cur))
    return chunks or [" ".join(words)]


def _caption_filters(chunks, total_dur, workdir, y_pos):
    """Time-synced drawtext chain banao (textfile se - escaping problem nahi).

    Har chunk ko uske char-length ke proportion me time-window milta hai,
    isliye caption voice ke saath-saath chalti hai.
    """
    font = _find_caption_font("".join(chunks))
    total_chars = sum(len(c) for c in chunks) or 1
    filters = []
    t = 0.0
    for i, c in enumerate(chunks):
        dur = total_dur * len(c) / total_chars
        start = max(0.0, t - 0.05)          # chhota overlap (flicker na ho)
        end = min(t + dur + 0.10, total_dur + 0.2)
        tf = workdir / f"cap_{i:03d}.txt"
        tf.write_text(c, encoding="utf-8")
        filters.append(
            f"drawtext=fontfile={font}:textfile={tf.as_posix()}"
            f":fontcolor=white:fontsize=54:x=(w-text_w)/2:y={y_pos}"
            f":box=1:boxcolor=black@0.55:boxborderw=16"
            f":enable='between(t,{start:.2f},{end:.2f})'"
        )
        t += dur
    return ",".join(filters)


def _zoompan(variant, total_frames, out_w, out_h, zoom_amount=0.28):
    """Variant ke hisaab se zoompan filter expression banao."""
    cx = "iw/2-(iw/zoom/2)"   # horizontal center
    cy = "ih/2-(ih/zoom/2)"   # vertical center
    n = max(total_frames, 1)
    zmax = round(1 + zoom_amount, 4)

    if variant == "zoom_in":
        z, x, y = f"min(1+{zoom_amount}*on/{n},{zmax})", cx, cy
    elif variant == "zoom_out":
        z, x, y = f"max({zmax}-{zoom_amount}*on/{n},1.0)", cx, cy
    elif variant == "pan_up":
        z, x, y = f"{zmax}", cx, f"(ih-ih/zoom)*(1-on/{n})"
    elif variant == "pan_down":
        z, x, y = f"{zmax}", cx, f"(ih-ih/zoom)*(on/{n})"
    elif variant == "pan_left":
        z, x, y = f"{zmax}", f"(iw-iw/zoom)*(1-on/{n})", cy
    elif variant == "pan_right":
        z, x, y = f"{zmax}", f"(iw-iw/zoom)*(on/{n})", cy
    elif variant == "ken_burns":
        z, x, y = f"min(1+{zoom_amount}*on/{n},{zmax})", f"(iw-iw/zoom)*(on/{n})", cy
    else:
        z, x, y = f"min(1+{zoom_amount}*on/{n},{zmax})", cx, cy

    return (
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1"
        f":s={out_w}x{out_h}:fps={FPS}"
    )


def _slow_music_source():
    """Slow ambient pad (Am -> F -> C -> G, har chord 4 sec).
    Pure ffmpeg synth hai isliye 100% copyright-free.

    - st(0) : chord index (0..3)
    - st(1) : per-chord fade in/out envelope (smooth transitions)
    - st(2)/st(3)/st(4) : chord ke 3 notes ki frequency
    - detune layer (+0.8 Hz) = warm sound
    """
    # poore expression ko single-quote me rakha hai - ffmpeg filtergraph
    # parser ke ; aur , se bichahta hai
    expr = (
        "st(0,floor(mod(t,16)/4));"
        "st(1,0.5-0.5*cos(2*PI*mod(t,4)/4));"
        "st(2,if(eq(ld(0),0),220,if(eq(ld(0),1),174.61,"
        "if(eq(ld(0),2),261.63,196))));"
        "st(3,if(eq(ld(0),0),261.63,if(eq(ld(0),1),220,"
        "if(eq(ld(0),2),329.63,246.94))));"
        "st(4,if(eq(ld(0),0),329.63,if(eq(ld(0),1),261.63,"
        "if(eq(ld(0),2),392,293.66))));"
        "ld(1)*0.20*(sin(2*PI*ld(2)*t)+sin(2*PI*ld(3)*t)+sin(2*PI*ld(4)*t))"
        "+ld(1)*0.08*(sin(2*PI*(ld(2)+0.8)*t)+sin(2*PI*(ld(4)+0.8)*t))"
        "+0.012*(2*random(0)-1)*ld(1)"
    )
    return f"aevalsrc='{expr}':s=44100"


def edit_video(src, audio, out_path, variant, effects_cfg, script=None):
    """Source video + TTS audio se final 9:16 Short banao. Output path return.

    script diya to time-synced captions bhi lagti hain (Hinglish ya
    Devanagari - text ke hisaab se font khud choose hota hai). Jo voice
    bolti hai wahi text video pe dikhta hai.
    """
    src, audio, out_path = Path(src), Path(audio), Path(out_path)
    duration = get_duration(src)
    audio_dur = get_duration(audio)
    speed = float(effects_cfg.get("video_speed", 1.12))
    bgm_volume = float(effects_cfg.get("bgm_volume", 0.10))
    zoom_amount = float(effects_cfg.get("zoom_amount", 0.28))

    out_duration = min(duration / speed, audio_dur)
    total_frames = int(out_duration * FPS) + 1
    pad_y = (TARGET_H - BAND_H) // 2   # 240px white upar + neeche

    vf = (
        # 1. video thodi tez (speed)
        f"setpts=PTS/{speed},"
        # 2. 2x upscale (zoom quality ke liye)
        f"scale={BIG_W}:{BIG_H}:force_original_aspect_ratio=increase:flags=lanczos,"
        # 3. CROP: TOP se anchor (y=0) - upar ka content safe, neeche ka
        #    hissa (jahan English caption hota hai) kat jata hai
        f"crop={BIG_W}:{BIG_H}:0:0,"
        f"fps={FPS},"
        # 4. zoom / pan effect
        f"{_zoompan(variant, total_frames, BAND_W, BAND_H, zoom_amount)},"
        # 5. color grade: vibrant + punchy + thoda bright
        f"eq=saturation={effects_cfg.get('saturation', 1.28)}"
        f":contrast={effects_cfg.get('contrast', 1.12)}"
        f":brightness={effects_cfg.get('brightness', 0.04)},"
        # 6. sharpness (crisp look)
        f"unsharp=5:5:{effects_cfg.get('sharpen', 1.0)},"
        # 7. white canvas (upar-neeche background)
        f"pad={TARGET_W}:{TARGET_H}:0:{pad_y}:white,"
    )

    # 8. time-synced captions (Hinglish ya Devanagari - font auto)
    if script:
        chunks = _caption_chunks(script)
        # caption neeche wali strip me - band ke andar (band: 240..1680)
        vf += _caption_filters(chunks, out_duration, out_path.parent, 1560) + ","

    vf += "setsar=1"

    # ---- audio: voice (loudness normalize) + slow background music ----
    if bgm_volume > 0:
        af = (
            f"[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
            f"[2:a]volume={bgm_volume},lowpass=f=1100[bg];"
            f"[voice][bg]amix=inputs=2:duration=first:normalize=0[a]"
        )
    else:
        af = "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"

    cmd = [
        FFMPEG, "-y",
        "-i", str(src),
        "-i", str(audio),
    ]
    if bgm_volume > 0:
        cmd += ["-f", "lavfi", "-t", f"{out_duration + 2:.2f}",
                "-i", _slow_music_source()]

    cmd += [
        "-filter_complex", f"[0:v]{vf}[v];{af}",
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264", "-preset", effects_cfg.get("preset", "medium"),
        "-crf", "18",
        "-threads", str(effects_cfg.get("threads", 0)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]
    _run(cmd)
    return out_path
