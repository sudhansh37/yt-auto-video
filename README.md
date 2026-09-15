# Hindi Shorts Bot (Zack D. Films -> Hindi YouTube Shorts)

Automated pipeline: **Zack D. Films ki shorts -> Gemini analysis -> Hindi TTS
voice -> 9:16 white-background edit (zoom/pan effects) -> YouTube Shorts
scheduled upload (din me 2 baar)**. Sab kuch GitHub Actions pe chalta hai.

> Note: ye repo pehle se lage secrets use karta hai — `GEMINI_API_KEY`,
> `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`. Koi nayi key
> lagane ki zaroorat nahi hai.

```
channel se short pick (duplicate check)      [src/downloader.py + src/history.py]
        |
        v
yt-dlp se download
        |
        v
Gemini se video analysis -> Hindi script    [src/analyzer.py]
+ title + description (JSON me)
        |
        v
edge-tts se Hindi voice                     [src/tts.py]
(original audio hata diya jata hai)
NO-GAP fix: script cleanup + fast rate,
taaki voice robotic na lage
        |
        v
ffmpeg edit:                                [src/editor.py]
  - 1080x1920 (9:16 Shorts/Reels format)
  - upar-neeche WHITE background
  - zoom in / zoom out / pan up / pan down / ken burns effect
  - color filter (saturation + contrast)
        |
        v
YouTube pe Short upload                     [src/uploader.py]
        |
        v
history.json save (ye video dobara nahi)    -> GitHub pe auto-commit
```

## Publish time change karna

`.github/workflows/daily.yml` me `cron:` lines edit karo.
**cron UTC me hota hai, IST se 5:30 ghante peeche.**

| Aapka IST time | cron (UTC)     |
|----------------|----------------|
| 09:00 AM       | `"0 3 * * *"`  |
| 10:00 AM       | `"30 4 * * *"` |
| 01:00 PM       | `"30 7 * * *"` |
| 06:00 PM       | `"30 12 * * *"` |
| 09:00 PM       | `"30 15 * * *"` |

Default: **10:00 AM + 06:00 PM IST** (din me 2 videos).

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

## TTS / voice

- Default: **edge-tts** (free) — `hi-IN-MadhurNeural`, `rate: "+10%"`.
  Voice change karni ho to `config.yaml` me `tts.edge_tts.voice`
  (options: `hi-IN-MadhurNeural`, `hi-IN-SwaraNeural`, `hi-IN-HemaNeural`).
- **Robotic/gap problem ka fix**: `src/tts.py` ka `clean_for_speech()` script
  me se ellipses, dashes, extra commas, paragraph breaks hata deta hai
  (ye sab TTS me lambi pause banate hain). Gemini prompt bhi "no pause
  markers" ke liye set hai. Zyada/kam speed chahiye to `rate` adjust karo.
- Apna TTS API lagana ho: `config.yaml` me `provider: "custom_http"` karke
  endpoint/keys bharo (template ready hai).

## Download me "Sign in to confirm you're not a bot" aa jaye to

Code me backup hai (tv/ios/mweb clients try karta hai). Phir bhi aaye to:
1. Apne browser me YouTube login karke [cookies.txt export](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) karo
2. Repo -> Settings -> Secrets -> Actions -> naya secret `YOUTUBE_COOKIES`
   me us file ka poora content paste kar do. Bas, ab download hamesha chalega.

## Effects (config.yaml me)

| Variant     | Kya karta hai                              |
|-------------|--------------------------------------------|
| `zoom_in`   | dheere dheere zoom in (1.00 -> 1.12)       |
| `zoom_out`  | dheere dheere zoom out (1.12 -> 1.00)      |
| `pan_up`    | halka zoom + camera neeche se upar         |
| `pan_down`  | halka zoom + camera upar se neeche         |
| `ken_burns` | zoom in + left-to-right pan                |

Har video pe randomly ek laga hai; saturation/contrast bhi configurable hai.

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
