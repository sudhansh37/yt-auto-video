"""YouTube cookies valid hain ya nahi - check karo.

Har ghante (cookie-check.yml) chalta hai. YOUTUBE_COOKIES secret se
cookies.txt banata hai, YouTube pe logged-in state check karta hai, aur
data/cookie_status.json likhta hai. Expired hone par workflow issue kholta
hai (GitHub owner ko mail jaata hai) aur agent cron Gmail pe bhi mail bhejta hai.

Exit code hamesha 0 - ye ek MONITOR hai, fail nahi hona chahiye.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

COOKIES = Path("cookies.txt")
STATUS = Path("data/cookie_status.json")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check():
    """(expired: bool, reason: str)"""
    raw = os.environ.get("YOUTUBE_COOKIES", "")
    if not raw.strip():
        return True, "YOUTUBE_COOKIES secret set nahi hai"
    rows = [l for l in raw.splitlines() if l.count("\t") >= 6]
    if not rows:
        return True, "cookies me koi valid cookie row nahi mili"
    COOKIES.write_text(raw, encoding="utf-8")
    try:
        r = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "60", "-b", str(COOKIES),
             "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
             "https://www.youtube.com/"],
            capture_output=True, text=True, timeout=90,
        )
        html = r.stdout or ""
    except Exception as e:  # noqa: BLE001
        return False, f"check nahi ho paya ({str(e)[:80]}) - cookies present hain"
    finally:
        COOKIES.unlink(missing_ok=True)

    if '"LOGGED_IN":true' in html or "'LOGGED_IN':true" in html:
        return False, "logged in - cookies theek hain"
    if "/signin" in html or "accounts.google.com" in html:
        return True, "YouTube ne logged-out dikhaya - cookies expire ho gaye"
    return False, "ambiguous response - cookies present hain (alert nahi)"


def main():
    expired, reason = _check()
    status = {
        "checked_at": _now(),
        "expired": expired,
        "reason": reason,
        "cookie_rows": len([l for l in os.environ.get("YOUTUBE_COOKIES", "").splitlines()
                            if l.count("\t") >= 6]),
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(json.dumps(status))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"expired={'yes' if expired else 'no'}\n")
            f.write(f"reason={reason}\n")


if __name__ == "__main__":
    main()
