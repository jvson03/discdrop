import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
MP3 = OUT / "mp3"
WAV = OUT / "wav"
BURNER = BASE / "burner" / "burn.ps1"

for d in (OUT, MP3, WAV):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder="static")

# ------------------------------------------------ global session state
session = {
    "items": [],          # accepted songs
    "jobs": {},           # download jobs: id -> {state, total, done, items, created}
    "burns": {},          # burn jobs: id -> {state, message, created}
}
lock = threading.Lock()

AUDIO_SECONDS = 44.1 * 1000 * 2  # bytes/sec for 44.1kHz 16-bit stereo (WAV data)
CD_74 = 74 * 60                  # typical 650MB CD-R
CD_80 = 80 * 60                  # typical 700MB CD-R


def slugify(text, maxlen=60):
    text = re.sub(r'[\\/:*?"<>|]', "", text).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > maxlen:
        text = text[:maxlen].rstrip()
    return text or "track"


# ------------------------------------------------ search
_ytmusic = None


def get_ytmusic():
    global _ytmusic
    if _ytmusic is None:
        try:
            from ytmusicapi import YTMusic
            _ytmusic = YTMusic()
        except Exception:
            _ytmusic = False
    return _ytmusic


def parse_duration(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        parts = raw.split(":")
        try:
            secs = 0
            for p in parts:
                secs = secs * 60 + int(p)
            return secs
        except ValueError:
            return None
    return None


def search_with_ytmusic(query, n=3):
    yt = get_ytmusic()
    if not yt:
        return None
    try:
        res = yt.search(query, filter="songs", limit=n)
        out = []
        for e in res:
            vid = e.get("videoId")
            if not vid:
                continue
            artists = ", ".join(a.get("name", "") for a in e.get("artists", []) or [])
            out.append({
                "id": vid,
                "query": query,
                "title": e.get("title", query),
                "artist": artists or "Unknown",
                "album": e.get("album", {}).get("name", "") if e.get("album") else "",
                "duration": parse_duration(e.get("duration")),
                "thumb": (e.get("thumbnails") or [{}])[-1].get("url", ""),
                "src": "ytmusic",
            })
            if len(out) >= n:
                break
        return out
    except Exception:
        return None


def search_with_ytdlp(query, n=3):
    import yt_dlp
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "extract_flat": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    out = []
    for e in (info.get("entries") or []):
        if not e.get("id"):
            continue
        out.append({
            "id": e["id"],
            "query": query,
            "title": e.get("title", query),
            "artist": e.get("artist") or e.get("channel") or "Unknown",
            "album": e.get("album", ""),
            "duration": e.get("duration"),
            "thumb": e.get("thumbnail", ""),
            "src": "youtube",
        })
    return out


@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json(force=True) or {}
    titles = [t.strip() for t in data.get("titles", []) if t.strip()]
    if not titles:
        return jsonify({"error": "No titles given."}), 400

    results = []
    for t in titles:
        got = search_with_ytmusic(t) or search_with_ytdlp(t)
        if not got:
            results.append({"query": t, "error": "no results", "candidates": []})
        else:
            results.append({"query": t, "error": None, "candidates": got})
    return jsonify({"results": results})


# ------------------------------------------------ download
def ffmpeg(args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                   check=True, capture_output=True)


def run_download(job_id):
    job = session["jobs"][job_id]
    try:
        total = len(job["items"])
        for idx, it in enumerate(job["items"]):
            if job["state"] == "cancelled":
                return
            it["status"] = "working"
            job["current"] = it["title"]
            try:
                out_mp3, out_wav = fetch_and_convert(it)
                it["mp3"] = str(out_mp3)
                it["wav"] = str(out_wav)
                it["status"] = "done"
            except Exception as exc:
                it["status"] = "error"
                it["error"] = str(exc)
            job["done"] = idx + 1
        job["state"] = "done"
    except Exception as exc:
        job["state"] = "error"
        job["error"] = str(exc)


def fetch_and_convert(it):
    import yt_dlp
    vid = it["id"]
    url = f"https://music.youtube.com/watch?v={vid}"
    raw_dir = OUT / "raw"
    raw_dir.mkdir(exist_ok=True)
    raw_path = raw_dir / f"{vid}.%(ext)s"
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(raw_path),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    raws = list(raw_dir.glob(f"{vid}.*"))
    if not raws:
        raise RuntimeError("yt-dlp produced no file")
    raw = raws[0]

    base = slugify(f"{it.get('artist','')} - {it['title']}")
    mp3_path = MP3 / f"{base}.mp3"
    wav_path = WAV / f"{base}.wav"

    meta = ["-metadata", f"title={it['title']}",
            "-metadata", f"artist={it.get('artist','')}",
            "-metadata", f"album={it.get('album','')}"]
    ffmpeg(["-i", str(raw), "-vn", "-ac", "2", "-ar", "44100", "-b:a", "320k",
            "-id3v2_version", "3", *meta, str(mp3_path)])
    ffmpeg(["-i", str(raw), "-vn", "-ac", "2", "-ar", "44100",
            "-c:a", "pcm_s16le", str(wav_path)])
    raw.unlink(missing_ok=True)
    return mp3_path, wav_path


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True) or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "No songs selected."}), 400
    with lock:
        job_id = uuid.uuid4().hex[:8]
        job = {
            "id": job_id,
            "state": "working",
            "total": len(items),
            "done": 0,
            "current": "",
            "items": [
                {"id": it.get("id"), "title": it.get("title"),
                 "artist": it.get("artist", ""), "album": it.get("album", ""),
                 "duration": it.get("duration"), "status": "queued"}
                for it in items
            ],
        }
        session["jobs"][job_id] = job
        session["items"] = job["items"]
    threading.Thread(target=run_download, args=(job_id,), daemon=True).start()
    return jsonify({"job": job_id})


@app.route("/api/status/<job_id>")
def api_status(job_id):
    job = session["jobs"].get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify({
        "state": job["state"],
        "total": job["total"],
        "done": job["done"],
        "current": job["current"],
        "items": job["items"],
        "error": job.get("error"),
    })


@app.route("/api/clear", methods=["POST"])
def api_clear():
    with lock:
        session["jobs"].clear()
        session["items"].clear()
        for f in list(MP3.glob("*")):
            f.unlink()
        for f in list(WAV.glob("*")):
            f.unlink()
    return jsonify({"ok": True})


# ------------------------------------------------ burn
def ps(args):
    full = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BURNER), *args]
    return subprocess.run(full, capture_output=True, text=True)


def run_burn(burn_id, drive, mode, ids, speed_kb):
    job = session["burns"][burn_id]
    try:
        staging = OUT / f"burn_{burn_id}"
        staging.mkdir(exist_ok=True)
        files = []
        for i, item_id in enumerate(ids):
            it = next((x for x in session["items"] if x["id"] == item_id), None)
            if not it:
                continue
            if mode == "audio" and it.get("wav") and Path(it["wav"]).exists():
                dst = staging / f"{i + 1:03d}_{slugify(it['title'], 40)}.pcm"
                ffmpeg(["-i", str(it["wav"]), "-f", "s16le", "-ac", "2", "-ar", "44100", str(dst)])
                files.append(dst)
            elif mode == "data" and it.get("mp3") and Path(it["mp3"]).exists():
                dst = staging / f"{i + 1:03d}_{slugify(it['title'], 30)}.mp3"
                shutil.copy(it["mp3"], dst)
                files.append(dst)
        if not files:
            job["state"] = "error"
            job["message"] = "No downloaded files to burn."
            return

        total_secs = 0
        for item_id in ids:
            it = next((x for x in session["items"] if x["id"] == item_id), None)
            if it and it.get("duration"):
                total_secs += int(it["duration"])
        if mode == "audio" and total_secs > CD_80:
            job["state"] = "error"
            job["message"] = f"Total play time {total_secs // 60}:{total_secs % 60:02d} exceeds an 80-minute CD."
            return

        job["message"] = "Starting burner..."
        args = ["-Mode", mode, "-Device", drive, "-StagingDir", str(staging)]
        if speed_kb:
            args += ["-SpeedKB", str(speed_kb)]
        result = ps(args)
        job["log"] = result.stdout + result.stderr
        if result.returncode == 0:
            job["state"] = "done"
            job["message"] = "Burning complete!"
        else:
            job["state"] = "error"
            job["message"] = result.stderr.strip() or result.stdout.strip() or "Burning failed."
    except Exception as exc:
        job["state"] = "error"
        job["message"] = str(exc)


@app.route("/api/drives")
def api_drives():
    result = ps(["-Mode", "Audio", "-CheckOnly"])
    if result.returncode != 0:
        return jsonify({"ok": False, "error": result.stderr.strip()})
    try:
        return jsonify(json.loads(result.stdout.strip().splitlines()[-1]))
    except Exception:
        return jsonify({"ok": False, "error": result.stdout + result.stderr})


@app.route("/api/burn", methods=["POST"])
def api_burn():
    data = request.get_json(force=True) or {}
    drive = data.get("drive")
    mode = data.get("mode", "audio")
    ids = data.get("ids", [])
    speed_kb = int(data.get("speed_kb") or 0)
    if not drive or not ids:
        return jsonify({"error": "Need drive and song ids."}), 400
    with lock:
        burn_id = uuid.uuid4().hex[:8]
        session["burns"][burn_id] = {"id": burn_id, "state": "working",
                                     "message": "queued", "log": "", "created": time.time()}
    threading.Thread(target=run_burn, args=(burn_id, drive, mode, ids, speed_kb), daemon=True).start()
    return jsonify({"burn": burn_id})


@app.route("/api/burnstatus/<burn_id>")
def api_burnstatus(burn_id):
    job = session["burns"].get(burn_id)
    if not job:
        return jsonify({"error": "unknown burn"}), 404
    return jsonify({"state": job["state"], "message": job["message"], "log": job.get("log", "")})


# ------------------------------------------------ ui
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    print("DiscDrop running at http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)