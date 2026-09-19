"""
ffmpeg editor v3 - Hindi Short banata hai:

  1. Video ko 3:4 band me CROP karo (1080x1440) - upar-neeche WHITE canvas
  2. Zoom/pan effect - ab zyada VISIBLE (default zoom 1.00 -> 1.28)
  3. Color grade: saturation + contrast + BRIGHTNESS + SHARPNESS (unsharp)
  4. Source video ka English caption WHITE PATTI se cover hota hai,
     aur usi patti pe HINGLISH text dikhta hai (jo TTS bol raha hai,
     time-synced chunks me)
  5. Video thodi tez (speed) + voice loudness normalize
  6. SLOW lo-fi style background music (ffmpeg synth - copyright free)
  7. Original audio HATA ke sirf Hindi TTS voice
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

TARGET_W, TARGET_H, FPS = 1080, 1920, 30
BAND_W, BAND_H = 1080, 1440   # video band (3:4 crop) - iske upar/neeche white
BIG_W, BIG_H = 2160, 2880     # zoom se pehle 2x upscale (quality ke liye)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
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
    elif variant == "ken_burns":
        z, x, y = f"min(1+{zoom_amount}*on/{n},{zmax})", f"(iw-iw/zoom)*(on/{n})", cy
    else:
        z, x, y = f"min(1+{zoom_amount}*on/{n},{zmax})", cx, cy

    return (
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1"
        f":s={out_w}x{out_h}:fps={FPS}"
    )


def _find_font():
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    return None


def _escape_drawtext(text):
    """drawtext ke liye special characters escape karo."""
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace(",", "\\,")
    text = text.replace("%", "\\%")
    text = text.replace("'", "").replace('"', "")
    return text


def _caption_lines(caption):
    """Lambi caption ko 2 lines me wrap karo (cut na ho)."""
    caption = caption.strip()
    if len(caption) <= 26 or " " not in caption:
        return [caption]
    mid = len(caption) // 2
    spaces = [i for i, ch in enumerate(caption) if ch == " "]
    split_at = min(spaces, key=lambda i: abs(i - mid))
    return [caption[:split_at].strip(), caption[split_at:].strip()]


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


def _chunk_hinglish(text, max_words=4):
    """Hinglish script ko chhote chunks me todo (screen pe readable)."""
    words = [w for w in text.split() if w.strip()]
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def _timed_chunks(chunks, total_s):
    """Chunks ko audio duration me char-count ke hisaab se baanto."""
    if not chunks or total_s <= 0:
        return []
    weights = [max(len(c), 6) for c in chunks]
    total_w = sum(weights)
    out, t = [], 0.0
    for c, w in zip(chunks, weights):
        d = total_s * w / total_w
        out.append((c, t, t + d))
        t += d
    return out


def edit_video(src, audio, out_path, variant, effects_cfg,
               caption=None, hinglish=None):
    """Source video + TTS audio se final 9:16 Short banao. Output path return."""
    src, audio, out_path = Path(src), Path(audio), Path(out_path)
    duration = get_duration(src)
    audio_dur = get_duration(audio)
    speed = float(effects_cfg.get("video_speed", 1.12))
    bgm_volume = float(effects_cfg.get("bgm_volume", 0.10))
    zoom_amount = float(effects_cfg.get("zoom_amount", 0.28))

    out_duration = duration / speed
    total_frames = int(out_duration * FPS) + 1
    pad_y = (TARGET_H - BAND_H) // 2   # 240px white upar + neeche

    vf = (
        # 1. video thodi tez (speed)
        f"setpts=PTS/{speed},"
        # 2. 2x upscale (zoom quality ke liye)
        f"scale={BIG_W}:{BIG_H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={BIG_W}:{BIG_H},"
        f"fps={FPS},"
        # 3. zoom / pan effect (ab zyada visible)
        f"{_zoompan(variant, total_frames, BAND_W, BAND_H, zoom_amount)},"
        # 4. color grade: vibrant + punchy + thoda bright
        f"eq=saturation={effects_cfg.get('saturation', 1.28)}"
        f":contrast={effects_cfg.get('contrast', 1.12)}"
        f":brightness={effects_cfg.get('brightness', 0.04)},"
        # 5. sharpness (crisp look)
        f"unsharp=5:5:{effects_cfg.get('sharpen', 1.0)},"
        # 6. white canvas (upar-neeche background)
        f"pad={TARGET_W}:{TARGET_H}:0:{pad_y}:white,"
        "setsar=1"
    )

    # 7. ENGLISH CAPTION COVER - white patti video band ke center pe,
    #    Zack D. Films style caption wahi hota hai
    band_cfg = effects_cfg.get("caption_band", {}) or {}
    band_y = pad_y + int(BAND_H * float(band_cfg.get("y", 0.30)))
    band_h = int(BAND_H * float(band_cfg.get("height", 0.40)))
    if band_cfg.get("enabled", True) and band_h > 0:
        color = band_cfg.get("color", "white")
        vf += f",drawbox=x=0:y={band_y}:w=iw:h={band_h}:color={color}:t=fill"

    # 8. HOOK caption (top white band pe, bada bold text)
    font = _find_font()
    if caption and font:
        cap = str(caption).strip()[:48]
        lines = _caption_lines(cap)
        n = len(lines)
        for idx, line in enumerate(lines):
            text = _escape_drawtext(line)
            fontsize = 62 if len(line) <= 24 else (54 if len(line) <= 32 else 46)
            if n == 1:
                y = pad_y // 2 - fontsize // 2
            else:
                y = 38 + idx * (fontsize + 16)
            vf += (
                f",drawtext=fontfile={font}:text='{text}'"
                f":fontcolor=black:fontsize={fontsize}"
                f":x=(w-text_w)/2:y={y}"
            )

    # 9. HINGLISH TEXT patti ke upar - TTS jo bol raha hai, time-synced
    if font and hinglish and str(hinglish).strip() and band_cfg.get("enabled", True):
        chunks = _chunk_hinglish(
            str(hinglish), int(band_cfg.get("words_per_line", 4))
        )
        show_dur = min(audio_dur, out_duration) if audio_dur > 0 else out_duration
        band_center = band_y + band_h // 2
        for text, t0, t1 in _timed_chunks(chunks, show_dur):
            esc = _escape_drawtext(text)
            fs = int(band_cfg.get("font_size", 64))
            if len(text) > 20:
                fs -= 8
            if len(text) > 28:
                fs -= 8
            fs = max(fs, 34)
            vf += (
                f",drawtext=fontfile={font}:text='{esc}'"
                f":fontcolor=black:fontsize={fs}"
                f":x=(w-text_w)/2:y={band_center - fs // 2}"
                f":enable='between(t,{t0:.2f},{t1:.2f})'"
            )

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
