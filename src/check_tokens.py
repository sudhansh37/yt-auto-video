"""YouTube tokens ki jaanch - dono channels ka refresh token chal raha hai ya nahi?

Chalane ka tarika: repo -> Actions -> "check-tokens" -> Run workflow.
Har channel ke liye saaf OK / FAIL + exact reason batata hai (aur agar FAIL ho
to kya karna hai). Poora pipeline chalane ki zaroorat nahi - 20 second me
pata chal jata hai.

Channel 1 -> YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN
Channel 2 -> YT_CLIENT_ID_2 / YT_CLIENT_SECRET_2 / YT_REFRESH_TOKEN_2
"""
import os
import sys

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
NAMES = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")


def check(channel):
    """Ek channel ka token test karo -> (ok: bool, message: str)."""
    suffix = "" if channel == 1 else f"_{channel}"
    env = {n: os.environ.get(n + suffix) for n in NAMES}
    missing = [n + suffix for n, v in env.items() if not v]
    if missing:
        return False, f"secrets set nahi hain: {', '.join(missing)}"

    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": env["YT_CLIENT_ID"],
                "client_secret": env["YT_CLIENT_SECRET"],
                "refresh_token": env["YT_REFRESH_TOKEN"],
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"network error: {e}"

    if resp.status_code == 200:
        return True, "OK - token valid hai (upload ready)"

    body = resp.text[:200].replace("\n", " ")
    hint = ""
    if "invalid_grant" in resp.text:
        hint = ("  -> FIX: naya refresh token banao (access_type=offline + "
                "prompt=consent) aur repo secret update karo. Yaad rakho: "
                "OAuth app ko dobara authorize karne par purana token "
                "invalid ho jata hai.")
    return False, f"HTTP {resp.status_code}: {body}{hint}"


def main():
    all_ok = True
    for ch in (1, 2):
        ok, msg = check(ch)
        all_ok = all_ok and ok
        print(f"Channel {ch}: {'OK  ' if ok else 'FAIL'} - {msg}")
    print()
    print("RESULT:", "dono channels theek hain" if all_ok
          else "kuch channel FAIL hai - upar ka reason dekho")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
