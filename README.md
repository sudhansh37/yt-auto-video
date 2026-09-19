# Hindi Shorts Bot (Zack D. Films -> Hindi YouTube Shorts)

Automated pipeline: **Zack D. Films ki shorts -> Gemini analysis -> IndicF5 Hindi
voice -> 9:16 edit (English caption cover + Hinglish caption + effects + slow
music) -> YouTube Shorts scheduled upload (din me 4 baar)**. Sab kuch GitHub
Actions pe chalta hai.

> Note: ye repo pehle se lage secrets use karta hai — `GEMINI_API_KEY`,
> `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`, `HF_TOKEN`.
> Koi nayi key lagane ki zaroorat nahi hai.

```
channel se short pick (duplicate check)      [src/downloader.py + src/history.py]
        |
        v
yt-dlp se download
        |
        v
Gemini se video analysis -> Hindi script    [src/analyzer.py]
+ Hinglish script + title + description
+ 503 "high demand" aaye to retry + fallback models
        |
        v
IndicF5 se near-human Hindi voice           [src/tts.py]
(AI4Bharat official space -> mirrors -> Parler -> edge-tts fallback chain)
(original audio hata diya jata hai)
        |
        v
ffmpeg edit:                                [src/editor.py]
  - 1080x1920 (9:16 Shorts/Reels format)
  - upar-neeche WHITE background
  - English caption pe WHITE PATTI + uspe time-synced Hinglish caption
    (jo TTS bol raha hai wahi screen pe dikhta hai)
  - strong zoom in / zoom out / pan / ken burns (zoom_amount: 0.28)
  - color grade: saturation + contrast + brightness
  - sharpness filter (unsharp)
  - slow lo-fi background music (ffmpeg synth - copyright free)
        |
        v
YouTube pe Short upload                     [src/uploader.py]
        |
        v
history.json save (ye video dobara nahi)    -> GitHub pe auto-commit
```

## Publish time change karna

`.github/workflows/daily.yml` me `cron:` lines edit karo.
**cron UTC me hota hai, IST se 5 ghante 30 minute peeche.**

Default: **din me 4 videos, subah 8 se shaam 5 ke beech** —
08:15 AM, 11:00 AM, 01:45 PM, 04:30 PM IST.

| Aapka IST time | cron (UTC)     |
|----------------|-----------------|
| 08:15 AM       | `"45 2 * * *"`  |
| 11:00 AM       | `"30 5 * * *"`  |
| 01:45 PM       | `"15 8 * * *"`  |
| 04:30 PM       | `"0 11 * * *"`  |

## Manual run (pehli baar test)

Repo -> **Actions** -> `publish-short` -> **Run workflow** -> mode choose
karo (`mix` / `latest` / `random`).

Pehli baar test karte waqt `config.yaml` me `youtube.privacy: "private"`
rakh lo — output pasand aaye tab `public` kar dena.

## Video selection modes

- `latest` — channel ki sabse nayi (unused) short
- `random` — koi bhi unused short, random
- `mix` (default) — 50% latest, 50% random purani

Duplicate kabhi nahi hota: `data/history.json` track karta hai, har run ke
baad GitHub pe commit hota hai. `max_history: 300` full hone pe reset.

## TTS / voice (IndicF5)

- Default: **AI4Bharat IndicF5** — near-human natural Hindi. Voice-clone
  model hai, isliye AI4Bharat ke official prompt audios use hote hain.
- Voice change karni ho to `config.yaml` me `tts.indicf5.voice`:
  - `punjabi_female_happy` (default — energetic female)
  - `tamil_female_happy`, `kannada_female_happy` (female)
  - `marathi_female_wiki` (calm female), `marathi_male_wiki` (male)
- **Fallback chain** (koi bhi step fail ho to agla automatic):
  1. `ai4bharat/IndicF5` (official HF space)
  2. do running mirrors (code me `INDICF5_SPACES` list)
  3. `ai4bharat/indic-parler-tts` (Aman voice)
  4. edge-tts (Madhur voice, koi API nahi)
- `HF_TOKEN` secret laga hona chahiye — rate-limit kam rehti hai.

## Caption patti (English caption cover)

Source video me jo English text hota hai use `editor.py` ek **white patti**
se cover karta hai aur usi patti pe **Hinglish caption** dikhta hai — wahi
jo TTS bol rahi hai, time-synced chunks me (har chunk audio ke hisaab se).

`config.yaml` me adjust kar sakte ho:

```yaml
caption_band:
  enabled: true
  y: 0.30        # patti kahan se shuru ho (video band ka fraction)
  height: 0.40   # kitni unchi ho
  color: "white" # patti ka color
  font_size: 64  # Hinglish text ka size
  words_per_line: 4
```

## Effects aur color grade (config.yaml me)

| Setting      | Kya karta hai                                  |
|--------------|-----------------------------------------------|
| `zoom_amount`| zoom/pan ki strength (0.28 = clearly visible)  |
| `saturation` | color vibrancy (1.28 default)                  |
| `contrast`   | punchy blacks (1.12 default)                   |
| `brightness` | thoda bright (0.04 default)                    |
| `sharpen`    | sharpness / crisp look (1.0 default)           |
| `video_speed`| video thodi tez (1.12 default)                 |
| `bgm_volume` | slow background music volume (0.10 default)   |

Effects: `zoom_in`, `zoom_out`, `pan_up`, `pan_down`, `ken_burns` — har
video pe randomly ek. Background music **slow ambient pad** hai (Am-F-C-G
chords) jo ffmpeg se synthesize hota hai — 100% copyright-free.

## Download me "Sign in to confirm you're not a bot" aa jaye to

Code me backup hai (tv/ios/mweb clients try karta hai). Phir bhi aaye to:
1. Apne browser me YouTube login karke [cookies.txt export](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) karo
2. Repo -> Settings -> Secrets -> Actions -> naya secret `YOUTUBE_COOKIES`
   me us file ka poora content paste kar do. Bas, ab download hamesha chalega.

## Purana topic-bot (backup)

Is repo ke purane files (`generate_and_upload.py`, `pipeline.py`,
`topics.txt`, `youtube_upload.py`) waise hi pade hain. `daily.yml` ab
naya Zack D. Films pipeline chalata hai. Purana topic-bot wapas chahiye
ho to workflow me `python generate_and_upload.py` wapas daal dena.

## ⚠️ Copyright note

Zack D. Films ki videos ke rights original creator ke paas hain. Unki
video lekar Hindi voice ke saath re-upload karna **copyright strike ya
channel termination ka risk** rakhta hai. Chhote clips + substantial apna
Hindi commentary rakhna hi safe hai; poora risk apni responsibility pe.
