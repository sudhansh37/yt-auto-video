"""ffmpeg editor v2 - Instagram-style Short banata hai:

  1. Video ko 3:4 band me CROP karo (1080x1440) - "crop karke format" look
  2. White canvas pe center (upar 240px + neeche 240px WHITE background)
  3. Upar wali white band pe HINGLISH CAPTION (bada bold black text)
  4. Zoom in / zoom out / pan up / pan down / ken burns effect
     (2x upscale ke baad zoom = smooth, sharp, no jitter)
  5. Color filter (saturation + contrast)
  6. Video SPEED thodi tez (default 1.15x - "mehnat wala" feel)
  7. Voice: loudness normalize (clear punchy audio) + halka background beat
  8. Original audio HATA ke sirf Hindi TTS voice
"""
import json
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


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg/ffprobe fail hua:\n{result.stderr[-2000:]}")
    return result


def get_duration(path):
    out = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    return float(json.loads(out.stdout)["format"]["duration"])


def _zoompan(variant, total_frames, out_w, out_h):
    """Variant ke hisaab se zoompan filter expression banao."""
    cx = "iw/2-(iw/zoom/2)"   # horizontal center
    cy = "ih/2-(ih/zoom/2)"   # vertical center
    n = max(total_frames, 1)

    if variant == "zoom_in":
        z, x, y = f"min(1+0.12*on/{n},1.12)", cx, cy
    elif variant == "zoom_out":
        z, x, y = f"max(1.12-0.12*on/{n},1.0)", cx, cy
    elif variant == "pan_up":
        z, x, y = "1.15", cx, f"(ih-ih/zoom)*(1-on/{n})"
    elif variant == "pan_down":
        z, x, y = "1.15", cx, f"(ih-ih/zoom)*(on/{n})"
    elif variant == "ken_burns":
        z, x, y = f"min(1+0.15*on/{n},1.15)", f"(iw-iw/zoom)*(on/{n})", cy
    else:
        z, x, y = f"min(1+0.12*on/{n},1.12)", cx, cy

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
    """Lambi caption ko 2 lines me wrap karo (cut na ho).
    1 line: ~26 chars tak, warna beech ke space se tod do."""
    caption = caption.strip()
    if len(caption) <= 26 or " " not in caption:
        return [caption]
    mid = len(caption) // 2
    spaces = [i for i, ch in enumerate(caption) if ch == " "]
    split_at = min(spaces, key=lambda i: abs(i - mid))
    return [caption[:split_at].strip(), caption[split_at:].strip()]


def _beat_source():
    """Halka background beat (120 BPM kick + offbeat hat) - copyright-free,
    kyunki ye synth se generate hota hai, koi gaana nahi."""
    return (
        "aevalsrc=0.8*sin(2*PI*60*mod(t\\,0.5))*exp(-18*mod(t\\,0.5))"
        "+0.06*(2*random(0)-1)*exp(-60*mod(t+0.25\\,0.5)):s=44100"
    )


def edit_video(src, audio, out_path, variant, effects_cfg, caption=None):
    """Source video + TTS audio se final 9:16 Short banao. Output path return."""
    src, audio, out_path = Path(src), Path(audio), Path(out_path)
    duration = get_duration(src)
    speed = float(effects_cfg.get("video_speed", 1.15))
    bgm_volume = float(effects_cfg.get("bgm_volume", 0.18))

    # speed ke baad actual output duration
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
        # 3. zoom / pan effect
        f"{_zoompan(variant, total_frames, BAND_W, BAND_H)},"
        # 4. color filter
        f"eq=saturation={effects_cfg.get('saturation', 1.15)}"
        f":contrast={effects_cfg.get('contrast', 1.08)},"
        # 5. white canvas (upar-neeche white background)
        f"pad={TARGET_W}:{TARGET_H}:0:{pad_y}:white,"
        "setsar=1"
    )

    # 6. Hinglish caption (top white band pe, lambi ho to 2 lines me)
    font = _find_font()
    if caption and font:
        caption = caption.strip()[:48]
        lines = _caption_lines(caption)
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

    # ---- audio: voice (loudness normalize) + halka beat mix ----
    if bgm_volume > 0:
        af = (
            f"[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
            f"[2:a]volume={bgm_volume}[bg];"
            f"[voice][bg]amix=inputs=2:duration=first:normalize=0[a]"
        )
    else:
        af = "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-i", str(audio),
    ]
    if bgm_volume > 0:
        cmd += ["-f", "lavfi", "-t", f"{out_duration + 2:.2f}", "-i", _beat_source()]

    cmd += [
        "-filter_complex", f"[0:v]{vf}[v];{af}",
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264", "-preset", effects_cfg.get("preset", "medium"),
        "-crf", "18",                        # better quality (20 se 18)
        "-threads", str(effects_cfg.get("threads", 0)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]
    _run(cmd)
    return out_path
