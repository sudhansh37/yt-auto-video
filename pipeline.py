"""
Core Video Pipeline - GitHub Actions ke liye (no Gradio, no web UI)
====================================================================
Topic se video banata hai: Gemini -> Images -> TTS -> FFmpeg -> video.mp4
"""

import os
import json
import random
import subprocess
import asyncio
import requests
import edge_tts
from mutagen.mp3 import MP3
from PIL import Image
from io import BytesIO
import tempfile
import shutil
import re

# FFmpeg path
try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = "ffmpeg"

W, H = 1080, 1920  # 9:16 Shorts
FPS = 25
EFFECTS = ["zoom_in", "zoom_out", "pan_right", "pan_left", "pan_up", "pan_down"]
TRANSITIONS = ["fade", "slideleft", "slideright", "slideup", "circleopen", "wipeleft"]


def generate_scenes(topic, api_key, num_scenes=6):
    """Gemini API se script likhwata hai - multiple models try karta hai"""
    
    # Multiple model names try karo (Google names change karta hai)
    models = [
        "gemini-3.6-flash",
        "gemini-flash-latest",
        "gemini-3.5-flash",
        "gemini-2.5-flash",
    ]
    
    prompt = f"""You are a professional YouTube Shorts script writer.
Write an engaging script about: "{topic}"

Rules:
- Total duration should be 30-50 seconds when narrated
- Break into exactly {num_scenes} short scenes
- Narration should be in Hinglish (Hindi written in English letters)
- Each scene narration should be 1-2 sentences (5-8 seconds when spoken)
- image_prompt should be in English, describing a visual that matches the narration
- Make it engaging, fast-paced, and informative

ReTURN ONLY a valid JSON array, no markdown:
[
  {{"narration": "Namaste dosto...", "image_prompt": "Indian person waving, bright background"}},
  ...
]"""

    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.8
        }
    }

    last_error = ""
    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        print(f"   Trying model: {model_name}...")
        
        try:
            response = requests.post(url, json=data, timeout=30)
            result = response.json()
            
            # Check for error response
            if "error" in result:
                error_msg = result["error"].get("message", "Unknown error")
                error_code = result["error"].get("code", "?")
                print(f"   -> Error {error_code}: {error_msg[:200]}")
                
                # 503 = high demand, retry after waiting
                if error_code == 503:
                    import time
                    print(f"   -> Waiting 15 sec and retrying...")
                    time.sleep(15)
                    response = requests.post(url, json=data, timeout=30)
                    result = response.json()
                    if "candidates" in result:
                        text = result["candidates"][0]["content"]["parts"][0]["text"]
                        print(f"   -> Success with {model_name} (after retry)!")
                        text = text.strip()
                        if text.startswith("```"):
                            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                        scenes = json.loads(text)
                        return scenes
                
                last_error = f"Model {model_name}: {error_msg}"
                continue
            
            # Check for candidates
            if "candidates" not in result:
                print(f"   -> No 'candidates' in response. Full response: {str(result)[:300]}")
                last_error = f"Model {model_name}: No candidates in response"
                continue
            
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            print(f"   -> Success with {model_name}!")
            
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            
            scenes = json.loads(text)
            return scenes
            
        except Exception as e:
            print(f"   -> Exception: {e}")
            last_error = f"Model {model_name}: {str(e)}"
            continue
    
    # All models failed
    raise Exception(f"Gemini API se script nahi ban paya. Last error: {last_error}\n"
                     f"Check karo: API key sahi hai ya nahi? aistudio.google.com/apikey")


def generate_image(prompt, hf_token, idx, output_dir):
    """Pollinations.ai se image banata hai (free, no API key needed)"""
    import urllib.parse
    
    # Pollinations.ai - free image generation, no API key
    enhanced_prompt = prompt + ", high quality, cinematic lighting, 4k, detailed, vibrant colors"
    encoded_prompt = urllib.parse.quote(enhanced_prompt)
    seed = random.randint(1, 999999)
    
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=768&height=1344&nologo=true&seed={seed}"
    
    for attempt in range(3):
        try:
            print(f"      Pollinations attempt {attempt+1}...")
            response = requests.get(url, timeout=60)
            if response.status_code == 200 and 'image' in response.headers.get('Content-Type', ''):
                image = Image.open(BytesIO(response.content))
                image_path = os.path.join(output_dir, f"scene_{idx}.png")
                image.save(image_path)
                return image_path
            else:
                print(f"      Status: {response.status_code}")
                import time
                time.sleep(5)
        except Exception as e:
            print(f"      Error: {e}")
            import time
            time.sleep(5)
    
    print(f"      Using placeholder image")
    colors = [(30, 60, 120), (120, 30, 60), (60, 120, 30), (120, 60, 30)]
    img = Image.new("RGB", (768, 1344), color=colors[idx % len(colors)])
    image_path = os.path.join(output_dir, f"scene_{idx}.png")
    img.save(image_path)
    return image_path


def generate_voice(text, output_path, voice="hi-IN-MadhurNeural"):
    """Edge TTS se voiceover"""
    async def _gen():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
    asyncio.run(_gen())
    return output_path


def get_audio_duration(audio_path):
    audio = MP3(audio_path)
    return audio.info.length


def _get_filter(effect, duration_sec):
    total_frames = int(duration_sec * FPS)
    z_speed = 0.0015
    
    if effect == "zoom_in":
        vf = f"zoompan=z='min(zoom+{z_speed},1.5)':d={total_frames}:s={W}x{H}:fps={FPS}"
    elif effect == "zoom_out":
        vf = f"zoompan=z='if(eq(on,0),1.5,max(zoom-{z_speed},1.0))':d={total_frames}:s={W}x{H}:fps={FPS}"
    elif effect == "pan_right":
        vf = f"zoompan=z=1.3:x='(iw-iw/zoom)*on/{total_frames}':y='ih/2-(ih/zoom/2)':d={total_frames}:s={W}x{H}:fps={FPS}"
    elif effect == "pan_left":
        vf = f"zoompan=z=1.3:x='(iw-iw/zoom)*(1-on/{total_frames})':y='ih/2-(ih/zoom/2)':d={total_frames}:s={W}x{H}:fps={FPS}"
    elif effect == "pan_up":
        vf = f"zoompan=z=1.3:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(1-on/{total_frames})':d={total_frames}:s={W}x{H}:fps={FPS}"
    elif effect == "pan_down":
        vf = f"zoompan=z=1.3:x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*on/{total_frames}':d={total_frames}:s={W}x{H}:fps={FPS}"
    else:
        vf = f"zoompan=z='min(zoom+{z_speed},1.3)':d={total_frames}:s={W}x{H}:fps={FPS}"
    return vf


def create_video_clip(image_path, duration, output_path, effect=None):
    """Image se video clip (no audio) with Ken Burns effect"""
    if effect is None:
        effect = random.choice(EFFECTS)
    
    vf = _get_filter(effect, duration)
    scale_crop = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    full_filter = f"{scale_crop},{vf}"
    
    cmd = [FFMPEG, "-y", "-loop", "1", "-i", image_path,
           "-vf", full_filter, "-c:v", "libx264", "-preset", "ultrafast",
           "-t", str(duration), "-pix_fmt", "yuv420p", "-an", output_path]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        cmd_fb = [FFMPEG, "-y", "-loop", "1", "-i", image_path,
                  "-vf", scale_crop, "-c:v", "libx264", "-preset", "ultrafast",
                  "-t", str(duration), "-pix_fmt", "yuv420p", "-an", output_path]
        subprocess.run(cmd_fb, capture_output=True, text=True, timeout=120)
    return effect


def concat_audio(audio_paths, output_path):
    list_file = output_path + ".txt"
    with open(list_file, "w") as f:
        for ap in audio_paths:
            f.write(f"file '{ap}'\n")
    cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
           "-c:a", "libpm3lame", "-b:a", "128k", output_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        cmd_copy = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                    "-c", "copy", output_path]
        subprocess.run(cmd_copy, capture_output=True, text=True, timeout=120)
    try:
        os.remove(list_file)
    except:
        pass


def merge_clips_with_audio(video_clips, audio_path, output_path):
    """Video clips ko merge karta hai + audio add karta hai (simple approach)"""
    
    # Approach 1: Simple concat demuxer (most reliable)
    # Create a list file and concat all clips, then add audio
    
    if len(video_clips) == 1:
        cmd = [FFMPEG, "-y", "-i", video_clips[0], "-i", audio_path,
               "-c:v", "libx264", "-preset", "ultrafast",
               "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
               "-shortest", output_path]
        subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return

    
    # Step 1: Concat all video clips using demuxer (no re-encode)
    list_file = output_path + "_list.txt"
    with open(list_file, "w") as f:
        for cp in video_clips:
            f.write(f"file '{cp}'\n")
    
    merged_video = output_path + "_merged.mp4"
    cmd_concat = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
              "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
              "-an", merged_video]
    result = subprocess.run(cmd_concat, capture_output=True, text=True, timeout=300)
    
    try:
        os.remove(list_file)
    except:
        pass
    
    if result.returncode == 0 and os.path.exists(merged_video):
        # Step 2: Add audio to merged video
        cmd_audio = [FFMPEG, "-y", "-i", merged_video, "-i", audio_path,
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                    "-shortest", output_path]
        result2 = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=120)
        os.remove(merged_video)
        
        if result2.returncode == 0 and os.path.exists(output_path):
            print("   -> Merge successful (concat + audio)")
            return
    
    # Step 3: Fallback - try xfade with pairs (2 clips at a time)
    print("   -> Concat failed, trying pairwise merge...")
    _pairwise_merge(video_clips, audio_path, output_path)


def _pairwise_merge(video_clips, audio_path, output_path):
    """Fallback: clips ko 2-2 karke merge karo"""
    
    # Step 1: Add each scene's audio to its clip, creating complete clips
    print("   -> Adding audio to each clip...")
    full_clips = []
    for i, (cp, ap) in enumerate(zip(video_clips, [])):
        full_clip = output_path + f"_full_{i}.mp4"
        cmd = [FFMPEG, "-y", "-i", cp, "-i", audio_path,
               "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
               "-shortest", full_clip]
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    
    # Step 2: Simple concat demuxer with re-encode
    list_file = output_path + "_pairlist.txt"
    with open(list_file, "w") as f:
        for i in range(len(video_clips)):
            f.write(f"file '{video_clips[i]}'\n")
    
    cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
           "-i", audio_path,
            "-c:v", "libx264", "-preset", "ultrafast",
           "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
            "-shortest", output_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    
    try:
        os.remove(list_file)
    except:
        pass
    
    if result.returncode != 0:
        print(f"   -> Pairwise merge error: {result.stderr[-300:]}")
        # Last resort: just take first clip + audio
        cmd = [FFMPEG, "-y", "-i", video_clips[0], "-i", audio_path,
               "-c:v", "libx264", "-preset", "ultrafast",
               "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
               "-shortest", output_path]
        subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def generate_video(topic, gemini_key, hf_token, voice="hi-IN-MadhurNeural"):
    """Pura pipeline: topic -> final video file. Returns (video_path, info_dict)"""
    work_dir = tempfile.mkdtemp(prefix="video_pipeline_")
    images_dir = os.path.join(work_dir, "images")
    audio_dir = os.path.join(work_dir, "audio")
    clips_dir = os.path.join(work_dir, "clips")
    for d in [images_dir, audio_dir, clips_dir]:
        os.makedirs(d, exist_ok=True)

    try:
        print(f"[1/6] Script likha ja raha hai (Gemini API)...")
        scenes = generate_scenes(topic, gemini_key, num_scenes=6)
        total_scenes = len(scenes)
        print(f"   -> {total_scenes} scenes ban gaye")

        print(f"[2/6] Images ban rahe hain (HF API)...")
        image_paths = []
        for i, s in enumerate(scenes):
            image_prompt = s.get("image_prompt", s.get("image", "abstract art"))
            print(f"   -> Image {i+1}/{total_scenes}...")
            img_path = generate_image(image_prompt, hf_token, i, images_dir)
            image_paths.append(img_path)
        print(f"   -> Saari images ready!")

        print(f"[3/6] Voiceover ban raha hai (Edge TTS)...")
        audio_paths = []
        durations = []
        for i, s in enumerate(scenes):
            narration = s.get("narration", s.get("text", ""))
            audio_path = os.path.join(audio_dir, f"scene_{i}.mp3")
            generate_voice(narration, audio_path, voice)
            dur = get_audio_duration(audio_path)
            audio_paths.append(audio_path)
            durations.append(dur)
        total_duration = sum(durations)
        print(f"   -> Total audio: {total_duration:.1f} sec")

        print(f"[4/6] Video clips ban rahe hain (FFmpeg effects)...")
        clip_paths = []
        used_effects = []
        for i in range(total_scenes):
            clip_path = os.path.join(clips_dir, f"clip_{i}.mp4")
            effect = create_video_clip(image_paths[i], durations[i], clip_path)
            used_effects.append(effect)
            print(f"   -> Clip {i+1}/{total_scenes}: {effect}")
        
        print(f"[5/6] Audio merge ho raha hai...")
        combined_audio = os.path.join(work_dir, "combined_audio.mp3")
        concat_audio(audio_paths, combined_audio)

        print(f"[6/6] Final video assemble ho rahi hai...")
        final_path = os.path.join(work_dir, "final_video.mp4")
        merge_clips_with_audio(clip_paths, combined_audio, final_path)

        # Copy to stable path
        output_path = os.path.join(os.getcwd(), "final_video.mp4")
        shutil.copy(final_path, output_path)

        # Build info
        narrations = [s.get("narration", s.get("text", "")) for s in scenes]
        title = topic[:60] + "..." if len(topic) > 60 else topic
        # Generate a catchy title from first scene
        catchy_title = narrations[0][:50] + "..." if len(narrations[0]) > 50 else narrations[0]
        
        description = f"""{topic}

Scene breakdown:
""" + "\n".join([f"{i+1}. {narrations[i]}" for i in range(total_scenes)])

        tags = topic.split()[:10]
        
        info = {
            "video_path": output_path,
            "title": catchy_title,
            "description": description,
            "tags": tags,
            "duration": total_duration,
            "scenes": narrations,
            "effects": used_effects,
        }
        
        print(f"\n✅ Video ready! Duration: {total_duration:.1f}s")
        print(f"   Path: {output_path}")
        print(f"   Title: {catchy_title}")
        
        return output_path, info

    except Exception as e:
        import traceback
        print(f"❌ Error: {e}")
        print(traceback.format_exc()[-500:])
        return None, None
    finally:
        try:
            shutil.rmtree(work_dir)
        except:
            pass
