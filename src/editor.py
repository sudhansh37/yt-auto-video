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
  7. QUALITY: x264 CRF 13 (near-lossless) + preset slow + 256k audio
     (config.yaml me crf / preset / audio_bitrate se control hota hai)
  8. LOW-RES AUTO-ENHANCE: agar source 1080px se kam ho (720p/480p...)
     to output automatic crisp 1080p ban jata hai - CAS (contrast-adaptive
     sharpening) + extra unsharp. 1080+ sources pe kuch extra nahi hota.
  9. Background music (halki, copyright-free ffmpeg synth) - sirf Hindi TTS
     voice ke saath. Original English audio BILKUL mix NAHI hota (feature
     poora hata diya gaya hai - audio 100% Hindi rehna chahiye).
 10. UPAR white area me MAZEDAAR Hinglish headline (top_text) - video ke
     baare me chhoti catchy line (config se on/off).
 11. TIME-SYNCED HINGLISH CAPTIONS - jo script Gemini likhta hai (jo voice
     bolti hai) wahi text video pe bottom me white-on-black dikhta hai.
     Captions HINGLISH (Roman letters) me hoti hain - Devanagari text aaye
     to Devanagari font automatically use hota hai.

Captions ka style: neeche wali strip me white text + halka kaala box
(subtitle style). Hinglish ke liye DejaVu Sans Bold, Devanagari ke liye
Noto Sans Devanagari Bold (CI pe fonts-noto-core package se aata hai).
Har chunk apne time-window me dikhta hai.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

TARGET_W, TARGET_H, FPS = 1080, 1920, 30
BAND_W, BAND_H = 1080, 1440   # video band (3:4 crop) - iske upar/neeche white
# zoom se pehle ka upscale. Max zoom 1.30x (config zoom_amount 0.30) hai,
# isliye 1.30x hi rakha: zoompan ka crop output band (1080x1440) ke barabar
# hai -> koi resample nahi, aur heavy region 6.2MP se 2.55MP (2.4x kam kaam).
# Pehle 2160x2880 (2x) tha - 360x640 source se koi asli detail nahi aata tha,
# sirf CPU/waqt khaata tha (is wajah se render 25 min+ le raha tha).
# NOTE: zoom_amount badlo to BIG = BAND x (1+zoom_amount) rakhna.
BIG_W, BIG_H = 1404, 1872

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


def _run(cmd, timeout=None):
    """ffmpeg/ffprobe chalao. timeout diya to usse zyada nahi chalega.

    Bina timeout ke ye call hamesha ke liye latak sakti hai (aaj ka job isi
    wajah se 74 min atka tha).
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"ffmpeg/ffprobe {timeout}s me khatam nahi hua (timeout) - "
            f"file kharab ho sakti hai.")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg fail hua:\n{result.stderr[-2000:]}")
    return result


def _parse_duration_from_stderr(stderr):
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not m:
        raise RuntimeError("Duration parse nahi hui (ffprobe bhi nahi mila).")
    h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mnt * 60 + s


def get_dimensions(path):
    """Video ki (width, height) - ffprobe se, warna ffmpeg -i ke stderr se."""
    if shutil.which("ffprobe"):
        out = _run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", str(path),
        ])
        streams = json.loads(out.stdout).get("streams") or []
        if streams:
            return int(streams[0]["width"]), int(streams[0]["height"])
    # fallback: ffmpeg -i ka stderr parse karo
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True,
    )
    m = re.search(r"Video:.*?\s(\d{2,5})x(\d{2,5})", result.stderr)
    if not m:
        raise RuntimeError("Video resolution parse nahi hui.")
    return int(m.group(1)), int(m.group(2))


_FILTER_CACHE = {}


def _has_filter(name):
    """Is ffmpeg build me filter available hai? (ek baar check, phir cache).

    Zaroori hai kyunki 'cas' jaisa filter har build me nahi hota - na ho to
    render fail hone ke bajaye sirf unsharp se kaam chal jata hai.
    """
    if name not in _FILTER_CACHE:
        try:
            r = subprocess.run(
                [FFMPEG, "-hide_banner", "-h", f"filter={name}"],
                capture_output=True, text=True,
            )
            _FILTER_CACHE[name] = (r.returncode == 0
                                   and f"Filter {name}" in (r.stdout + r.stderr))
        except Exception:  # noqa: BLE001
            _FILTER_CACHE[name] = False
    return _FILTER_CACHE[name]


def get_duration(path):
    """ffprobe se duration; ffprobe na ho to ffmpeg -i se parse karo.

    Dono raste 60s timeout ke saath - kharab file pe kabhi hang nahi hoga.
    """
    if shutil.which("ffprobe"):
        out = _run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ], timeout=60)
        return float(json.loads(out.stdout)["format"]["duration"])
    # fallback: ffmpeg ka stderr parse karo (60s timeout)
    try:
        result = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", str(path), "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg duration check 60s me khatam nahi hua "
                           "(file kharab lagti hai).")
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


def _top_text_filters(text, workdir, color="yellow", border=4,
                      outline="black"):
    """FALLBACK: drawtext se headline (jab PIL/emoji font na ho).

    Bold + outline look (screenshot jaisa). Emoji nahi aata - isliye jab
    possible ho to _headline_png() use hota hai.
    """
    text = " ".join(str(text).split())[:48]
    if not text:
        return ""
    font = _find_caption_font(text)
    size = 54 if len(text) <= 26 else (46 if len(text) <= 34 else 38)
    tf = workdir / "top_text.txt"
    tf.write_text(text, encoding="utf-8")
    return (
        f"drawtext=fontfile={font}:textfile={tf.as_posix()}"
        f":fontcolor={color}:fontsize={size}:x=(w-text_w)/2:y=(240-text_h)/2"
        f":borderw={int(border)}:bordercolor={outline}"
    )


def _headline_png(text, workdir, color="yellow", outline="black",
                  fontsize=64, max_width=1000):
    """Headline ko transparent PNG me render karo (PIL) - COLOUR EMOJI ke saath.

    ffmpeg drawtext colour emoji nahi dikhata (box aa jata hai), isliye
    headline PIL se image me banti hai aur ffmpeg overlay se lagti hai.
    Yellow bold text + black outline (screenshot jaisa) + emoji.
    PIL/emoji font na mile to None return hota hai (drawtext fallback chalta hai).
    """
    try:
        import glob as _glob

        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001
        return None

    text = " ".join(str(text).split())
    if not text:
        return None

    # aakhir me jo emoji/symbol hain unhe alag karo
    i = len(text)
    while i > 0 and ord(text[i - 1]) >= 0x2190:
        i -= 1
    main, emoji = text[:i].rstrip(), text[i:].strip()
    if not main:
        main, emoji = text, ""

    try:
        tfont = ImageFont.truetype(_find_caption_font(main or text), fontsize)
    except Exception:  # noqa: BLE001
        return None

    # colour emoji font (CI pe fonts-noto-color-emoji se aata hai)
    efont = None
    if emoji:
        cand = [os.environ.get("EMOJI_FONT") or "",
                "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
                "/usr/share/fonts/**/NotoColorEmoji.ttf"]
        for pat in cand:
            if not pat:
                continue
            hits = [pat] if Path(pat).exists() else sorted(
                _glob.glob(pat, recursive=True))
            if hits:
                try:
                    efont = ImageFont.truetype(hits[0], 109)
                except Exception:  # noqa: BLE001
                    efont = None
                break
    if efont is None:
        emoji = ""          # emoji font nahi -> emoji chhod do (box na aaye)

    stroke = 5
    probe = Image.new("RGBA", (10, 10))
    d0 = ImageDraw.Draw(probe)
    bb = d0.textbbox((0, 0), main, font=tfont, stroke_width=stroke)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]

    eimg = None
    ew = 0
    if emoji:
        try:
            eimg = Image.new("RGBA", (260, 260), (0, 0, 0, 0))
            ImageDraw.Draw(eimg).text((20, 20), emoji, font=efont,
                                      embedded_color=True)
            eimg = eimg.crop(eimg.getbbox())
            target = int(fontsize * 1.15)
            eimg = eimg.resize((target, target), Image.LANCZOS)
            ew = target + 16
        except Exception:  # noqa: BLE001
            eimg, ew = None, 0

    pad = 14
    W = min(max_width, tw + ew + pad * 2)
    H = max(th, fontsize) + pad * 2
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.text((pad - bb[0], pad - bb[1]), main, font=tfont, fill=color,
            stroke_width=stroke, stroke_fill=outline)
    if eimg is not None:
        img.paste(eimg, (pad + tw + 16, (H - eimg.height) // 2), eimg)

    out = workdir / "headline.png"
    img.save(out)
    return out


def edit_video(src, audio, out_path, variant, effects_cfg, script=None,
               top_text=None):
    """Source video + TTS audio se final 9:16 Short banao. Output path return.

    script diya to time-synced captions bhi lagti hain (Hinglish ya
    Devanagari - text ke hisaab se font khud choose hota hai). Jo voice
    bolti hai wahi text video pe dikhta hai.

    top_text diya to video ke UPAR (white area me) ek mazedaar headline
    dikhta hai - video ke baare me ek chhoti catchy line.

    AUDIO: sirf Hindi TTS voice (loudnorm) + halki background music.
    Original English audio kabhi mix NAHI hota.
    """
    src, audio, out_path = Path(src), Path(audio), Path(out_path)
    duration = get_duration(src)
    audio_dur = get_duration(audio)
    speed = float(effects_cfg.get("video_speed", 1.12))
    bgm_volume = float(effects_cfg.get("bgm_volume", 0.10))
    zoom_amount = float(effects_cfg.get("zoom_amount", 0.28))
    canvas = effects_cfg.get("canvas_color", "black")   # background colour

    out_duration = min(duration / speed, audio_dur)
    total_frames = int(out_duration * FPS) + 1
    pad_y = (TARGET_H - BAND_H) // 2   # 240px white upar + neeche

    # ---- LOW-RES AUTO-ENHANCE (detect) ----
    # source ki width 1080 se kam (720p/480p...) hai? To band 1080x1440 pe
    # upscale hoga aur soft dikhega - us case me auto-enhance chalega taaki
    # output crisp 1080p lage. 1080+ sources pe kuch extra nahi hota.
    src_w, src_h = get_dimensions(src)
    threshold = int(effects_cfg.get("lowres_threshold", 1080))
    enhance = (bool(effects_cfg.get("enhance_lowres", True))
               and src_w < threshold)
    if enhance:
        print(f"  [editor] Low-res source {src_w}x{src_h} (< {threshold}px "
              f"width) - auto 1080p enhancement ON")

    vf = (
        # 1. video thodi tez (speed)
        f"setpts=PTS/{speed},"
        # fps=30 yahin (heavy scale se PEHLE) - source 60fps ho to 60fps ke
        # saare frames par lanczos upscale karne se bachte hain (kaam ~2x kam).
        f"fps={FPS},"
        # 2. upscale (zoom ke liye - 1.30x, resample-free)
        f"scale={BIG_W}:{BIG_H}:force_original_aspect_ratio=increase:flags=lanczos,"
        # 3. CROP: TOP se anchor (y=0) - upar ka content safe, neeche ka
        #    hissa (jahan English caption hota hai) kat jata hai
        f"crop={BIG_W}:{BIG_H}:0:0,"
        # 4. zoom / pan effect
        f"{_zoompan(variant, total_frames, BAND_W, BAND_H, zoom_amount)},"
        # 5. color grade: vibrant + punchy + thoda bright
        f"eq=saturation={effects_cfg.get('saturation', 1.28)}"
        f":contrast={effects_cfg.get('contrast', 1.12)}"
        f":brightness={effects_cfg.get('brightness', 0.04)},"
        # 6. sharpness (crisp look)
        f"unsharp=5:5:{effects_cfg.get('sharpen', 1.0)},"
        # 7. background canvas (upar-neeche - default black)
        f"pad={TARGET_W}:{TARGET_H}:0:{pad_y}:{canvas},"
    )

    # ---- RED LINE (video aur background ke beech, screenshot jaisa) ----
    # Poori width ki patli line band ke top edge pe. drawbox core filter hai.
    bw = int(effects_cfg.get("border_width", 4))
    if bw > 0:
        bcolor = effects_cfg.get("border_color", "red")
        if effects_cfg.get("border_top", True):
            vf += (f"drawbox=x=0:y={pad_y - bw}:w=iw:h={bw}:"
                   f"color={bcolor}@1:t=fill,")
        if effects_cfg.get("border_bottom", False):
            vf += (f"drawbox=x=0:y={pad_y + BAND_H}:w=iw:h={bw}:"
                   f"color={bcolor}@1:t=fill,")

    # ---- exposure (thoda bright) - filter ho to hi lagta hai ----
    expo = float(effects_cfg.get("exposure", 0.0))
    if expo and _has_filter("exposure"):
        vf += f"exposure=exposure={expo},"

    # ---- LOW-RES AUTO-ENHANCE (upscaled content ko crisp 1080p look) ----
    # CAS = contrast-adaptive sharpening (upscaled video ke liye best).
    # 'cas' filter na ho to sirf extra unsharp chalta hai (render fail nahi hota).
    if enhance:
        parts = []
        if _has_filter("cas"):
            parts.append(f"cas=strength={effects_cfg.get('lowres_cas', 0.6)}")
        # NOTE: pehle yahan ek extra unsharp=7:7 bhi tha. Ab hata diya - base
        # chain ka unsharp=5:5 + ye cas kaafi hai, aur teen sharpen passes
        # (5:5 + cas + 7:7) sirf CPU/waqt khaate the (render 25min+ ki ek wajah).
        if parts:
            vf += ",".join(parts) + ","

    # 7.5 video ke UPAR colourful headline (bold + outline + emoji)
    headline_png = None
    if top_text:
        headline_png = _headline_png(
            top_text, out_path.parent,
            effects_cfg.get("overlay_text_color", "yellow"),
            effects_cfg.get("overlay_text_outline", "black"),
        )
        if headline_png is None:
            # PIL/emoji font na mila - drawtext fallback (bold + outline)
            vf += _top_text_filters(
                top_text, out_path.parent,
                effects_cfg.get("overlay_text_color", "yellow"),
                int(effects_cfg.get("overlay_text_border", 4)),
                effects_cfg.get("overlay_text_outline", "black"),
            ) + ","

    # 8. time-synced captions (Hinglish ya Devanagari - font auto)
    if script:
        chunks = _caption_chunks(script)
        # caption neeche wali strip me - band ke andar (band: 240..1680)
        vf += _caption_filters(chunks, out_duration, out_path.parent, 1560) + ","

    vf += "setsar=1"

    # ---- audio mixing (100% Hindi) ----
    # Hindi TTS voice (loudnorm) + halki background music.
    # Original English audio BILKUL mix nahi hota - audio hamesha Hindi
    # hi rehta hai (algorithm confuse na ho ki Hindi hai ya English).
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
    if headline_png:
        cmd += ["-loop", "1", "-i", str(headline_png)]

    # ---- QUALITY (max) - config se control hota hai ----
    crf = str(int(effects_cfg.get("crf", 13)))          # kam = behtar
    preset = effects_cfg.get("preset", "slow")           # slow = behtar
    audio_bitrate = effects_cfg.get("audio_bitrate", "256k")

    # headline PNG hai to use overlay se lagao (colour emoji ke saath)
    if headline_png:
        hidx = 3 if bgm_volume > 0 else 2
        vchain = (f"[0:v]{vf}[base];"
                  f"[base][{hidx}:v]overlay="
                  f"x=(main_w-overlay_w)/2:y=(240-overlay_h)/2:format=yuv420[v]")
    else:
        vchain = f"[0:v]{vf}[v]"

    cmd += [
        "-filter_complex", f"{vchain};{af}",
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", preset,            # slow = behtar quality (compression smart)
        "-crf", crf,                  # 13 = near-lossless
        "-profile:v", "high",
        # aq-mode=3 = dark scenes me behtar detail; rc-lookahead=20 se memory
        # bachti hai (default 60 bhaari padta hai - chhote runner pe render
        # OOM ho jata tha). Quality par koi asar nahi.
        "-x264-params", "aq-mode=3:rc-lookahead=20",
        "-threads", str(effects_cfg.get("threads", 0)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        "-shortest",
        str(out_path),
    ]
    _run(cmd, timeout=2100)   # render max 35 min (1500s bahut tight tha)
    return out_path
