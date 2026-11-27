"""Keep every daily reel for the app's Reels tab: the video goes to a GitHub Release named "reels" (outside the website,
so the site stays small) and docs/data/reels.json lists them, newest first. Needs the gh CLI and GH_TOKEN (cloud jobs)."""
import json
import os
import shutil
import subprocess

from warehouse import config

INDEX = os.path.join(config.ROOT, "docs", "data", "reels.json")
TAG = "reels"


def _gh(*args):
    return subprocess.run(["gh"] + list(args), capture_output=True, text=True, cwd=config.ROOT)


def publish(day, path, total, trades, won, lost, log=print):
    """Upload the video and add it to reels.json. Returns True when uploaded."""
    if not (shutil.which("gh") and (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))):
        log("reels: no gh / token here, not stored")
        return False
    name = "reel-%s.mp4" % day
    tmp = os.path.join(os.path.dirname(path), name)
    if tmp != path:
        shutil.copyfile(path, tmp)
    if _gh("release", "view", TAG).returncode != 0:
        r = _gh("release", "create", TAG, "--title", "Daily reels", "--notes", "Daily recap videos for the app's Reels tab.",
                "--latest=false")
        if r.returncode != 0:
            log("reels: could not create the release: %s" % r.stderr.strip()[:200])
            return False
    r = _gh("release", "upload", TAG, tmp, "--clobber")
    if r.returncode != 0:
        log("reels: upload failed: %s" % r.stderr.strip()[:200])
        return False
    doc = {"items": []}
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as fh:
            doc = json.load(fh)
    items = [x for x in doc.get("items", []) if x.get("date") != str(day)]
    items.append({"date": str(day), "file": name, "total": round(total), "trades": trades, "won": won, "lost": lost,
                  "kb": os.path.getsize(tmp) // 1024})
    doc["items"] = sorted(items, key=lambda x: x["date"], reverse=True)
    os.makedirs(os.path.dirname(INDEX), exist_ok=True)
    with open(INDEX, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    log("reels: stored %s" % name)
    return True
