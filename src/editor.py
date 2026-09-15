"""ffmpeg editor: 9:16 (1080x1920) white canvas + zoom/pan effects + color filter.

Pipeline:
  1. Video ko 1080x1920 ke andar fit karo (aspect ratio maintain, sirf shrink)
  2. White canvas pe center karo -> upar-neeche white background
  3. Constant 30 fps
  4. Chosen effect poore frame pe:
       zoom_in   : dheere dheere zoom in  (1.00 -> 1.12)
       zoom_out  : dheere dheere zoom out (1.12 -> 1.00)
       pan_up    : halka zoom ke saath camera neeche se upar slide
       pan_down  : halka zoom ke saath camera upar se neeche slide
       ken_burns : zoom in + right pan (classic documentary feel)
  5. Color filter (saturation + contrast se thoda vibrant/punchy)
  6. Original audio HATA ke sirf TTS Hindi voice mux hoti hai
"""
import json
import subprocess
from pathlib import Path

TARGET_W, TARGET_H, FPS = 1080, 1920, 30


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


def _zoompan(variant, total_frames):
    """Variant ke hisaab se zoompan filter expression banao."""
    cx = "iw/2-(iw/zoom/2)"   # horizontal center
    cy = "ih/2-(ih/zoom/2)"   # vertical center
    n = max(total_frames, 1)

    if variant == "zoom_in":
        z, x, y = f"min(1+0.12*on/{n},1.12)", cx, cy
    elif variant == "zoom_out":
        z, x, y = f"max(1.12-0.12*on/{n},1.0)", cx, cy
    elif variant == "pan_up":
        # camera neeche se upar jaati hai
        z, x, y = "1.15", cx, f"(ih-ih/zoom)*(1-on/{n})"
    elif variant == "pan_down":
        # camera upar se neeche jaati hai
        z, x, y = "1.15", cx, f"(ih-ih/zoom)*(on/{n})"
    elif variant == "ken_burns":
        # zoom in + left-to-right pan
        z, x, y = f"min(1+0.15*on/{n},1.15)", f"(iw-iw/zoom)*(on/{n})", cy
    else:
        # unknown variant -> safe default zoom_in
        z, x, y = f"min(1+0.12*on/{n},1.12)", cx, cy

    return (
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1"
        f":s={TARGET_W}x{TARGET_H}:fps={FPS}"
    )


def edit_video(src, audio, out_path, variant, effects_cfg):
    """Source video + TTS audio se final 9:16 Short banao. Output path return."""
    src, audio, out_path = Path(src), Path(audio), Path(out_path)
    duration = get_duration(src)
    total_frames = int(duration * FPS) + 1

    vf = (
        # 1. fit inside 1080x1920
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        # 2. white canvas pe center (upar-neeche white background)
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:white,"
        "setsar=1,"
        # 3. constant fps
        f"fps={FPS},"
        # 4. zoom / pan effect
        f"{_zoompan(variant, total_frames)},"
        # 5. color filter
        f"eq=saturation={effects_cfg.get('saturation', 1.12)}"
        f":contrast={effects_cfg.get('contrast', 1.06)}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-i", str(audio),
        "-filter_complex", f"[0:v]{vf}[v]",
        "-map", "[v]",
        "-map", "1:a",                       # sirf TTS voice (original audio hata)
        # Notes: 'preset'/'threads' config se override ho sakte hain
        # (kam memory wale machines ke liye "ultrafast" + 1 useful hai;
        #  GitHub Actions ke 7GB runner pe default bilkul theek hai)
        "-c:v", "libx264", "-preset", effects_cfg.get("preset", "medium"),
        "-crf", "20",
        "-threads", str(effects_cfg.get("threads", 0)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]
    _run(cmd)
    return out_path
