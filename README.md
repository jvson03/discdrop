# DiscDrop

Drop a playlist, get a CD for the old car.

DiscDrop is a **local web app** that turns a list of song titles into a burned CD.
It searches YouTube Music for the real songs, downloads them at high quality, and
burns them onto a CD-R using Windows' built-in burning engine (IMAPI2) — no extra
CD burning software required.

Runs entirely on your machine; nothing is uploaded anywhere except the song
searches/downloads themselves.

---

## Features

- **Song lookup** — paste any number of titles (one per line) or drop a `.txt`
  file; DiscDrop finds each song on YouTube Music and shows top candidates with
  thumbnails, artists, and durations so you pick the right one.
- **In-browser preview** — hit **▶** on any candidate to stream the actual audio
  before you commit to downloading it.
- **High-quality download** — each chosen track is saved as a **320 kbps MP3**
  (with title/artist/album tags) plus a **CD-perfect WAV** (44.1 kHz / 16-bit /
  stereo) under `output/`.
- **Duplicate detection** — entering the same song twice shows a "dupe of #N"
  badge in search and in the playlist, plus a warning chip.
- **Retry / swap failed downloads** — failed tracks land in a "Needs attention"
  list with their error; hit **Retry** to try again, **Swap** to pick a different
  match inline, or **✕** to drop it.
- **Playlist editor** — reorder tracks (↑/↓), remove tracks, see total play time
  with an over-74-min warning before you burn.
- **Two disc types** — standard Audio CD (plays in any CD player) or MP3 data
  disc (for MP3-capable head units).
- **Burn speed control** — slow 4x for old-car reliability, faster when you
  don't need it.
- **Live burn log** — watch staging, track-by-track progress, and finalization
  stream in real time.
- **Auto-eject** — the tray pops open when a burn finishes.
- **Playlist import** — paste a Spotify or Apple Music playlist link to load its
  tracklist (see below).
- **Runs offline-capable** — the whole tool is local; your files stay on your PC.

---

## Requirements

- **Windows 10/11** (uses IMAPI2; other OSes unsupported for burning)
- **Python 3.10+**
- **ffmpeg** on your PATH (download from gyan.dev/ffmpeg-builds if missing)
- A CD/DVD **writer** and blank **CD-R** discs

---

## Setup

### First run (easiest)

Double-click **`start.bat`**. It creates a virtual environment, installs
dependencies, and opens the app at **http://localhost:5000** in your browser.

### Manual setup

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python app.py
```

Then open **http://localhost:5000**.

> If you change the code, restart the app to pick up the changes (the server has
> auto-reload disabled).

---

## How to use

### 1 · Add songs
- Type/paste titles, **one per line** — `Nirvana - Smells Like Teen Spirit` or
  just `Smells Like Teen Spirit` both work.
- Or **drop a `.txt` file** onto the drop zone.
- Or **import a playlist** (below).

### 2 · Confirm matches
- Each title shows up to 3 candidate matches from YouTube Music.
- Click the one that's right (green highlight), then **Download selected**.
- Preview any candidate with **▶** first if you're unsure.

### 3 · Download
- Watch progress; each track becomes a 320 kbps MP3 + a CD WAV.
- Anything that fails moves to **Needs attention** in the burn step.

### 4 · Burn
- Pick your **drive**, **disc type**, and **speed**.
- Reorder/remove tracks in the playlist if needed.
- Insert a **blank CD-R** (the app tells you if the disc is missing or used) and
  hit **Burn CD**.
- The log streams live; when it's done the tray **ejects automatically**.

---

## Importing playlists

### Apple Music (no setup)
Paste an Apple Music playlist link (e.g. `music.apple.com/us/playlist/...`) into
the import box and hit **Import**. Works immediately — the tracklist is read
straight from the public page.

### Spotify (free one-time setup)
Spotify hides playlists from unauthenticated requests, so it needs free API
credentials, set up once (about 2 minutes):

1. Go to **https://developer.spotify.com/dashboard** and log in.
2. **Create App** — any name/description; set redirect URI to anything, e.g.
   `http://localhost:5000/callback`.
3. Copy the **Client ID** and **Client Secret**.
4. Paste a Spotify playlist link, click **Import**, and enter those two values
   in the box that appears (they're saved to `config.json` on this PC only —
   never shared or committed).
5. Import again and the tracklist loads.

> Imported tracks fill the textarea with `Artist - Title` lines and jump straight
> into search, where you still confirm each match.

---

## Disc types & old-car tips

| | Audio CD | MP3 data disc |
|---|---|---|
| Plays in | any CD player, incl. 1990s stereos | head units with MP3 CD support (mid-2000s+) |
| Capacity | ~74–80 min of music | ~10x more songs |
| Tracks | one per song | files (with artist/title names) |

- **Old car stereos**: use an **Audio CD** and burn **slow (4x)**. Slow burns give
  cheap players' weak error correction an easier job and reduce tracking jitter.
  Faster speeds are fine for MP3 discs.
- **Good discs matter.** Quality CD-Rs (Taiyo Yuden, Verbatim AZO) read reliably
  in old decks; bargain discs skip.
- The WAVs are standard CD-Audio (44.1 kHz / 16-bit / stereo). YouTube's source
  audio is lossy, so the disc's quality is capped by the source — this is the best
  those tracks can be.
- CD-R is write-once. To reuse discs you'd need CD-RW, which old decks often
  won't read reliably — stick with CD-R.

---

## Where your files go

```
output/
├── mp3/    ← 320 kbps MP3s (archive / MP3 discs)
├── wav/    ← CD-perfect WAVs (archive / audio-disc source)
└── raw/    ← temporary download cache (auto-cleaned)
```

DiscDrop is **burn-and-forget**: after a *successful* burn it deletes the staging
folder, the MP3s, and the WAVs, and clears the playlist — the disc is the archive.
If a burn fails, everything is kept so you can retry.

`config.json` (created only if you save Spotify credentials) holds those
credentials locally and is git-ignored.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No CD/DVD drives found" | Is the writer plugged in / powered on? |
| "No disc in the drive" | Insert a blank CD-R and it'll recheck on next load. |
| "Disc in drive is not blank" | That CD-R is used (CD-R is write-once). Grab a blank one. |
| "Songs need N sectors…" | Playlist is longer than the disc; drop tracks or split. |
| Burn is slow | Normal — a ~70 min audio disc takes 10–20 min at 4x. |
| Download failed | Check the error in **Needs attention**, then **Retry** or **Swap**. |
| Preview won't play | Some streams are region-blocked; the download usually still works. |
| MP3 disc won't play in car | The head unit needs MP3 CD support; otherwise use an Audio CD. |
| Everything fine but disc skips | Try a slower burn speed and a better brand of CD-R. |

---

## Under the hood

- **Backend**: Flask (Python) + `yt-dlp` (downloads) + `ytmusicapi` (song search)
  + `ffmpeg` (transcoding to MP3/WAV/raw PCM).
- **Burning**: Windows' built-in **IMAPI2** via `burner/burn.ps1` (PowerShell) —
  no third-party burning software. Audio tracks are written Track-at-Once from raw
  PCM; MP3 discs are ISO9660+Joliet data discs.
  - The burner talks to the recorder via its **unique device ID** (from
    `MsftDiscMaster2`) rather than the drive letter — drive-letter access makes
    `PrepareMedia` fail with `E_HANDLE` on many USB writers.
- **Frontend**: plain HTML/CSS/JS, no build step. Runs on your machine only.
- `burner/burn.ps1` can be run directly from PowerShell if you ever want to burn
  from your own WAV/MP3 folders.

---

## Project layout

```
app.py              Flask backend (search, download, burn APIs)
burner/burn.ps1     IMAPI2 CD burner + drive detection
static/             Web UI (index.html, app.js, style.css)
requirements.txt    Python dependencies
start.bat           One-click launcher (setup + run)
config.json         Local Spotify credentials (optional, git-ignored)
output/             Downloaded MP3s, WAVs, temp burn files
```

---

*For personal use. Respect copyright and YouTube's terms of service.*