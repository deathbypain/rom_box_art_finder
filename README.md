# ROM Box Art Finder

A desktop tool for matching retro game **cover art to local ROM backups**,
specifically adapted to the filesystem layout of the **SF3000** handheld
running the custom **[TreeFrog UI](https://github.com/tzubertowski/TreeFrogUI)**.

It pulls box art from the **[libretro-thumbnails](https://github.com/libretro)**
repository on GitHub and lets you attach the correct image to each of your ROMs.

> ⚠ **This is *not* a fire-and-forget automated tool.**
> Every image match requires **human approval**. The tool proposes ranked
> candidates; *you* make the final decision on each one.

---

## What it does

1. **Scans** a platform folder (e.g. `SNES/`) for ROM files that are missing a
   cover image.
2. **Ranks** the available libretro-thumbnails titles against each ROM name
   using fuzzy string matching (`rapidfuzz`).
3. **Shows** you the top matches with a live image preview in a Tkinter
   review window.
4. When you **accept** a match, it downloads the box art and saves it as
   `<platform folder>/.res/<rom name>.png`, which is where the TreeFrog UI
   looks for cover art.

---

## Supported platforms

Folder names are matched case-insensitively against the mapping in
[`config.py`](config.py). Aliases (e.g. `snes02`, `fc`, `md`) are also
recognized.

| Folder name | Console | libretro-thumbnails repo |
|-------------|---------|--------------------------|
| `A26` | Atari 2600 | `Atari_-_2600` |
| `GB` | Game Boy | `Nintendo_-_Game_Boy` |
| `NES` / `fc` / `nesq` / `nest` | NES | `Nintendo_-_Nintendo_Entertainment_System` |
| `PS1` / `ps1r` | PlayStation | `Sony_-_PlayStation` |
| `SEGA` / `md` | Mega Drive / Genesis | `Sega_-_Mega_Drive_-_Genesis` |
| `SMS` | Master System / Mark III | `Sega_-_Master_System_-_Mark_III` |
| `SNES` / `sfc` / `snes02` | SNES | `Nintendo_-_Super_Nintendo_Entertainment_System` |

Add new platforms by editing `REPO_MAP` and `ROM_EXT_MAP` in
[`config.py`](config.py).

---

## How it works

- **Scanning** ([`scanner.py`](scanner.py)) walks each platform folder,
  keeping only files with a recognized ROM extension (e.g. `.nes`, `.sfc`,
  `.iso`, `.zip`, `.7z`). PS1 `.cue`/`.bin` pairs are collapsed to the `.cue`
  file, and duplicate base names are de-duplicated so one ROM doesn't queue
  two covers.
- **Ranking** ([`matcher.py`](matcher.py)) normalizes both the ROM name and
  each thumbnail title (stripping region tags like `(USA)`, bracket notes
  like `[!]`, and noise words) and scores them with `rapidfuzz`.
  Tie-breaking favors a matching region tag and a closer token-count match.
  There is **no auto-accept threshold** by design.
- **Fetching** ([`fetcher.py`](fetcher.py)) fetches each repo's title list
  from the GitHub API (cached under `.cache/`) and downloads the chosen
  `Named_Boxarts/<title>.png` on demand.
- **Review UI** ([`gui.py`](gui.py)) is a single Tkinter window: pick a
  platform folder, step through the queue, preview candidates, and **Accept /
  Reject** each one. Network work runs on background threads.

Cover images are scaled so the longest side fits within `250x200`
(configurable via `MAX_SIZE`) before being saved.

---

## Getting started

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python main.py
```

> `requirements.txt` includes `rapidfuzz`, `Pillow`, and `requests`.

In the review window:

1. Point it at a platform folder (e.g. `D:\SF3000\SNES`).
2. It queues every ROM without a `.res/<name>.png` cover.
3. Preview and **Accept** the right image (or **Reject** to skip / rename).

Images are written to `<platform folder>/.res/`.

---

## Packaging

A PyInstaller spec is provided in
[`RomBoxArtFinder.spec`](RomBoxArtFinder.spec). In a frozen build the app
anchors its `.cache` directory next to the executable so it persists between
runs.

---

## Project layout

| File | Purpose |
|------|---------|
| [`main.py`](main.py) | Entry point; launches the Tkinter app |
| [`config.py`](config.py) | Platform mappings, ROM extensions, tunables |
| [`scanner.py`](scanner.py) | Finds ROMs missing covers; handles renames |
| [`matcher.py`](matcher.py) | Title normalization and candidate ranking |
| [`fetcher.py`](fetcher.py) | GitHub title lists and image downloads |
| [`gui.py`](gui.py) | Tkinter review window |
| [`download_covers.ps1`](download_covers.ps1) | Reference PowerShell script (original prototype) |
| [`tests/`](tests) | Test suite |
