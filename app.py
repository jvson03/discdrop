import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import requests
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


def norm_title(s):
    s = (s or "").lower()
    s = re.sub(r"\(feat\..*?\)", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def iso_duration(raw):
    if not isinstance(raw, str):
        return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", raw)
    if not m:
        return None
    h, mm, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mm * 60 + s


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
    seen = {}
    for i, res in enumerate(results):
        if res.get("error"):
            continue
        key = norm_title(titles[i])
        if key in seen:
            res["dupe_of"] = seen[key]
        else:
            seen[key] = i
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
        job_items = []
        for it in items:
            obj = {
                "id": it.get("id"), "title": it.get("title"),
                "artist": it.get("artist", ""), "album": it.get("album", ""),
                "duration": it.get("duration"), "status": "queued",
            }
            existing = next((x for x in session["items"] if x["id"] == obj["id"]), None)
            if existing:
                existing.update(obj)
                job_items.append(existing)
            else:
                session["items"].append(obj)
                job_items.append(obj)
        job = {
            "id": job_id,
            "state": "working",
            "total": len(job_items),
            "done": 0,
            "current": "",
            "items": job_items,
        }
        session["jobs"][job_id] = job
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


@app.route("/api/remove", methods=["POST"])
def api_remove():
    data = request.get_json(force=True) or {}
    iid = data.get("id")
    with lock:
        session["items"] = [x for x in session["items"] if x["id"] != iid]
    return jsonify({"ok": True})


# ------------------------------------------------ burn
def ps(args):
    full = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BURNER), *args]
    return subprocess.run(full, capture_output=True, text=True)


def cleanup_after_burn(burn_id):
    staging = OUT / f"burn_{burn_id}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    for f in list(MP3.glob("*")):
        f.unlink(missing_ok=True)
    for f in list(WAV.glob("*")):
        f.unlink(missing_ok=True)
    with lock:
        session["items"] = []
        session["jobs"].clear()


def run_burn(burn_id, drive, mode, ids, speed_kb):
    job = session["burns"][burn_id]
    job["log"] = []
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
                job["log"].append(f"Staging {i + 1}/{len(ids)}  {it['title']}")
                job["message"] = f"Staging {it['title']}…"
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

        job["message"] = "Starting burner…"
        args = ["-Mode", mode, "-Device", drive, "-StagingDir", str(staging), "-Eject"]
        if speed_kb:
            args += ["-SpeedKB", str(speed_kb)]
        full = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(BURNER), *args]
        proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            job["log"].append(line)
            job["message"] = line
        proc.wait()
        if proc.returncode == 0:
            job["state"] = "done"
            job["message"] = "Burning complete! Disc ejected — downloads cleared."
            cleanup_after_burn(burn_id)
        else:
            job["state"] = "error"
            job["message"] = "Burning failed — see log."
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
                                     "message": "queued", "log": [], "created": time.time()}
    threading.Thread(target=run_burn, args=(burn_id, drive, mode, ids, speed_kb), daemon=True).start()
    return jsonify({"burn": burn_id})


@app.route("/api/burnstatus/<burn_id>")
def api_burnstatus(burn_id):
    job = session["burns"].get(burn_id)
    if not job:
        return jsonify({"error": "unknown burn"}), 404
    return jsonify({
        "state": job["state"],
        "message": job["message"],
        "log": "\n".join(job.get("log", [])),
    })


@app.route("/api/songs")
def api_songs():
    items = []
    total = 0
    seen_titles = {}
    for it in session["items"]:
        has = it.get("status") == "done"
        if has and it.get("duration"):
            total += int(it["duration"])
        key = norm_title(it["title"])
        dupe = key in seen_titles
        if key not in seen_titles:
            seen_titles[key] = it["id"]
        items.append({
            "id": it["id"],
            "title": it["title"],
            "artist": it.get("artist", ""),
            "album": it.get("album", ""),
            "duration": it.get("duration"),
            "status": it.get("status"),
            "mp3": it.get("mp3"),
            "wav": it.get("wav"),
            "error": it.get("error"),
            "dupe": dupe,
        })
    return jsonify({"items": items, "total_secs": total})


@app.route("/api/preview/<video_id>")
def api_preview(video_id):
    import yt_dlp
    try:
        ydl_opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "format": "bestaudio/best",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=False)
        url = info.get("url")
        if not url and info.get("formats"):
            url = info["formats"][0].get("url")
        if not url:
            return jsonify({"ok": False, "error": "No stream URL available."})
        return jsonify({"ok": True, "url": url})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


# ---------------- playlist import (Spotify / Apple Music) ----------------
CONFIG_FILE = BASE / "config.json"
_spotify_token = {"token": None, "expires": 0}


def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def spotify_token(cfg):
    cid = cfg.get("spotify_client_id")
    cs = cfg.get("spotify_client_secret")
    if not cid or not cs:
        return None
    if _spotify_token["token"] and time.time() < _spotify_token["expires"] - 30:
        return _spotify_token["token"]
    r = requests.post("https://accounts.spotify.com/api/token",
                      data={"grant_type": "client_credentials"},
                      auth=(cid, cs), timeout=30)
    r.raise_for_status()
    j = r.json()
    _spotify_token["token"] = j["access_token"]
    _spotify_token["expires"] = time.time() + j.get("expires_in", 3600)
    return _spotify_token["token"]


@app.route("/api/import", methods=["POST"])
def api_import():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Paste a Spotify or Apple Music playlist link."}), 400
    if "music.apple.com" in url or "itunes.apple.com" in url:
        return import_apple(url)
    if "spotify.com" in url or url.startswith("spotify:playlist:"):
        return import_spotify(url)
    return jsonify({"error": "Not a recognized Spotify or Apple Music playlist link."}), 400


def _tokens(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"feat\.?", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return set(re.split(r"\s+", s.strip()))


def candidate_score(title, artist, duration, c):
    want_t, cand_t = _tokens(title), _tokens(c.get("title", ""))
    score = 0.0
    if want_t and cand_t:
        inter = want_t & cand_t
        score += 0.55 * (len(inter) / max(len(want_t), 1))
    if artist:
        want_a, cand_a = _tokens(artist), _tokens(c.get("artist", ""))
        if want_a and cand_a:
            inter = want_a & cand_a
            score += 0.35 * (len(inter) / max(len(want_a), 1))
    if duration and c.get("duration"):
        d = int(duration)
        ratio = min(d, c["duration"]) / max(d, c["duration"], 1)
        score += 0.10 * max(0.0, 1 - abs(ratio - 1) * 3)
    return score


@app.route("/api/import-match", methods=["POST"])
def api_import_match():
    data = request.get_json(force=True) or {}
    songs = data.get("songs", [])
    if not songs:
        return jsonify({"error": "No songs given."}), 400
    matched, unmatched = [], []
    for s in songs:
        title = s.get("title")
        artist = s.get("artist", "")
        dur = s.get("duration")
        if not title:
            continue
        query = f"{artist} {title}".strip() if artist else title
        cands = search_with_ytmusic(query) or search_with_ytdlp(query) or []
        best, best_score = None, 0.0
        for c in cands:
            sc = candidate_score(title, artist, dur, c)
            if sc > best_score:
                best, best_score = c, sc
        if best and best_score >= 0.5:
            matched.append({
                "id": best["id"], "title": best["title"], "artist": best["artist"],
                "album": best.get("album", ""), "duration": best.get("duration"),
            })
        else:
            unmatched.append({
                "title": title, "artist": artist,
                "reason": f"no confident match (best {best_score:.2f})",
            })
    return jsonify({"matched": matched, "unmatched": unmatched})


def _walk_apple(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk_apple(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_apple(v)


def import_apple(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception as exc:
        return jsonify({"error": f"Could not fetch that Apple Music page: {exc}"}), 400
    title = "Apple Music playlist"
    songs = []
    # 1) editorial playlists expose a schema.org MusicPlaylist block
    m = re.search(r'<script[^>]*id=schema:music-playlist[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            d = json.loads(m.group(1))
            title = d.get("name", title)
            songs = [{"title": t.get("name"), "artist": "", "duration": iso_duration(t.get("duration"))}
                     for t in d.get("track", []) if t.get("name")]
        except Exception:
            songs = []
    # 2) user playlists (pl.u-...) keep their tracks in serialized-server-data
    if not songs:
        m2 = re.search(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', html, re.S)
        if m2:
            try:
                data = json.loads(m2.group(1))
                seo = (data.get("data") or [{}])[0].get("data", {}).get("seoData", {})
                if seo.get("pageTitle"):
                    t = re.sub(r"\s*-\s*Apple Music\s*$", "", seo["pageTitle"]).strip()
                    t = re.sub(r"\s+by\s+[^-]+$", "", t).strip()
                    title = t or title
                seen = set()
                for obj in _walk_apple(data):
                    if not (obj.get("title") and obj.get("artistName")):
                        continue
                    if obj.get("contentDescriptor", {}).get("kind") != "song":
                        continue
                    sid = obj.get("contentDescriptor", {}).get("identifiers", {}).get("storeAdamID") or obj.get("title")
                    if sid in seen:
                        continue
                    seen.add(sid)
                    dur = obj.get("duration")
                    songs.append({
                        "title": obj.get("title"),
                        "artist": obj.get("artistName", ""),
                        "duration": int(dur / 1000) if isinstance(dur, (int, float)) else None,
                    })
            except Exception:
                songs = []
    if not songs:
        return jsonify({"error": "No tracklist found on that Apple Music page."}), 400
    return jsonify({"provider": "apple", "title": title, "songs": songs})


def import_spotify(url):
    cfg = load_config()
    tok = spotify_token(cfg)
    if not tok:
        return jsonify({
            "needs_creds": True,
            "error": "Spotify needs free API credentials. Get them at developer.spotify.com (Dashboard → Create App), then paste the Client ID and Client Secret below.",
        }), 400
    m = re.search(r"(?:open\.spotify\.com/playlist/|spotify:playlist:)([A-Za-z0-9]+)", url)
    if not m:
        return jsonify({"error": "Could not find a playlist ID in that Spotify link."}), 400
    pid = m.group(1)
    try:
        h = {"Authorization": f"Bearer {tok}"}
        r = requests.get(f"https://api.spotify.com/v1/playlists/{pid}", headers=h, timeout=30)
        r.raise_for_status()
        title = r.json().get("name", "Spotify playlist")
        songs = []
        offset = 0
        while True:
            r2 = requests.get(f"https://api.spotify.com/v1/playlists/{pid}/tracks",
                              headers=h, params={"limit": 100, "offset": offset}, timeout=30)
            r2.raise_for_status()
            items = r2.json().get("items", [])
            if not items:
                break
            for it in items:
                t = it.get("track")
                if not t or not t.get("name"):
                    continue
                artists = ", ".join(a.get("name", "") for a in t.get("artists", []))
                songs.append({"title": t["name"], "artist": artists,
                              "duration": int((t.get("duration_ms") or 0) / 1000)})
            offset += len(items)
            if len(items) < 100:
                break
        if not songs:
            return jsonify({"error": "Playlist is empty or private."}), 400
        return jsonify({"provider": "spotify", "title": title, "songs": songs})
    except requests.HTTPError as exc:
        return jsonify({"error": f"Spotify API error {exc.response.status_code}: {exc.response.text[:200]}"}), 400
    except Exception as exc:
        return jsonify({"error": f"Spotify import failed: {exc}"}), 400


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        cfg = load_config()
        if "spotify_client_id" in data:
            cfg["spotify_client_id"] = (data.get("spotify_client_id") or "").strip()
        if "spotify_client_secret" in data:
            cfg["spotify_client_secret"] = (data.get("spotify_client_secret") or "").strip()
        save_config(cfg)
        return jsonify({"ok": True})
    cfg = load_config()
    return jsonify({
        "spotify_configured": bool(cfg.get("spotify_client_id") and cfg.get("spotify_client_secret")),
    })


# ------------------------------------------------ ui
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    print("DiscDrop running at http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)