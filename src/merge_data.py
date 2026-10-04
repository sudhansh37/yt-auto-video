"""Conflict-safe data merge (daily.yml / watchdog.yml ke commit step ke liye).

PROBLEM
-------
`daily.yml` (publish-short) aur `watchdog.yml` dono `data/history.json` +
`data/publish_log.json` ko commit karte hain. Job chalte-chalte remote `main`
aage badh jata hai (dusra run push kar chuka hota hai), aur commit step ka
`git pull --rebase origin main` in JSON files ko auto-merge nahi kar paata:

    CONFLICT (content): Merge conflict in data/history.json
    CONFLICT (content): Merge conflict in data/publish_log.json
    error: could not apply ... -> exit 1 -> run RED

FIX
---
Ye script apne (working tree) data ko remote (`origin/main`) ke data ke saath
UNION kar deta hai aur wapas likh deta hai. Iske baad HEAD ko seedha remote
tip par le jaake commit kiya jata hai - koi conflict nahi, aur kisi bhi run ka
data LOST nahi hota (duplicate-upload wala risk bhi nahi).

Usage:
    python src/merge_data.py                    # origin/main ke saath merge
    python src/merge_data.py --remote origin/main
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
HISTORY_PATH = DATA_DIR / "history.json"
PUBLISH_LOG_PATH = DATA_DIR / "publish_log.json"


def _read_local(path: Path):
    """Working-tree file padho; na ho ya kharab ho to None."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _read_remote(ref: str, rel_path: str):
    """`git show <ref>:<path>` se file padho; na mile to None."""
    try:
        out = subprocess.run(
            ["git", "show", f"{ref}:{rel_path}"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        return json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def merge_history(local, remote) -> dict:
    """{video_id: date} ka union, oldest-first order me.

    Oldest-first order zaroori hai kyunki `trim_history` dict ke shuru se
    (sabse purani) entries hataata hai.
    """
    merged: dict = {}
    if isinstance(remote, dict):
        merged.update(remote)
    if isinstance(local, dict):
        merged.update(local)   # local (aaj ka run) same key par jeet-ta hai
    return dict(sorted(merged.items(), key=lambda kv: str(kv[1])))


def _entries(doc) -> list:
    if isinstance(doc, dict) and isinstance(doc.get("publishes"), list):
        return doc["publishes"]
    return []


def merge_publish_log(local, remote) -> dict:
    """`publishes` list ka union (dedup), date+time+ch se sorted."""
    dedup: dict = {}
    for entry in _entries(remote) + _entries(local):
        if not isinstance(entry, dict):
            continue
        key = (entry.get("date"), entry.get("time"), entry.get("ch", 1),
               entry.get("yt"))
        dedup[key] = entry      # local baad me aata hai -> local jeet-ta hai

    ordered = [dedup[k] for k in sorted(
        dedup, key=lambda k: (str(k[0]), str(k[1]), str(k[2]))
    )]

    out: dict = {}
    for src in (remote, local):
        if isinstance(src, dict):
            out.update({k: v for k, v in src.items() if k != "publishes"})
    out["publishes"] = ordered
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Union-merge bot data files.")
    ap.add_argument("--remote", default="origin/main",
                    help="git ref jiske data se merge karna hai (default origin/main)")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    local_hist = _read_local(HISTORY_PATH)
    remote_hist = _read_remote(args.remote, "data/history.json")
    merged_hist = merge_history(local_hist, remote_hist)
    HISTORY_PATH.write_text(
        json.dumps(merged_hist, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[merge] history.json: local={len(local_hist or {})} "
          f"remote={len(remote_hist or {})} -> merged={len(merged_hist)}")

    local_log = _read_local(PUBLISH_LOG_PATH)
    remote_log = _read_remote(args.remote, "data/publish_log.json")
    merged_log = merge_publish_log(local_log, remote_log)
    PUBLISH_LOG_PATH.write_text(
        json.dumps(merged_log, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[merge] publish_log.json: local={len(_entries(local_log))} "
          f"remote={len(_entries(remote_log))} -> "
          f"merged={len(merged_log['publishes'])}")


if __name__ == "__main__":
    main()
