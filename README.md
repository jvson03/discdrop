# DiscDrop

Drop a list of song titles, get a CD for the old car.

A local web app that searches YouTube Music for your songs, downloads them at high
quality, and burns them onto a CD using Windows' built-in burning engine (IMAPI2) —
no extra CD software needed.

## How to run

Requires: Python 3.10+, ffmpeg on PATH.

```
venv\Scripts\python app.py
```

Then open **http://localhost:5000**. Or just double-click `start.bat`.

## Workflow

1. **Add songs** — paste titles (one per line) or drop a `.txt` file onto the page.
2. **Confirm matches** — each title shows a few candidates; pick the right one.
3. **Download** — best-quality audio is downloaded, saved as 320 kbps MP3 and a
   CD-perfect WAV (44.1 kHz / 16-bit / stereo) in `output/mp3` and `output/wav`.
4. **Burn** — pick the drive, type, and speed, then burn.

## Two disc types (old car vs old-old car)

- **Audio CD** — plays in *any* CD player, including 1990s car stereos. One track
  per song. This is the default.
- **MP3 data disc** — holds ~10x more songs, but only plays in head units that
  support MP3 CDs (most from the mid-2000s on). If your stereo shows a disc menu
  or reads data discs, use this.

## Burn speed and quality

- For audio CDs on old stereos, burn **slow**: 4x–8x. Slow burns give cheap players'
  weak error correction an easier job and reduce tracking jitter. Max speed is fine
  for MP3 data discs.
- The WAVs are standard CD-Audio: 44.1 kHz, 16-bit, stereo. Source audio from
  YouTube is lossy, so the CD quality is capped by the source — this is the best
  those tracks can be.
- Use quality CD-R discs (Taiyo Yuden / Verbatim AZO are the classics). Cheap discs
  can skip in old players.

## Notes

- For personal use. Respect copyright and YouTube's terms.
- The first burn on any machine: if the drive reports errors, try a fresh blank
  CD-R and a slower speed.
- `burner/burn.ps1` can also be run directly from PowerShell if you want to burn
  from your own WAV/MP3 folders.