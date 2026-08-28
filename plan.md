# Plan: ROM Box Art Finder (Python + Tkinter)

## TL;DR
Build a small Python app that scans a local ROM directory, finds missing covers, ranks candidate boxarts from the libretro-thumbnails GitHub repos using rapidfuzz, and lets the user review a preview and accept/reject each match. Accepted images are scaled to fit within 250x200 (aspect preserved, no padding) and saved to `{platform}/.res/{rom}.png`.

## Confirmed decisions
- **GUI**: Tkinter (built-in), simple: input fields, image viewer, buttons.
- **Matching**: Hybrid — rapidfuzz ranks top N candidates; user reviews and picks.
- **Output**: `{platform}/.res/{rom}.png` (same convention as the PowerShell script).
- **Sizing**: Scale longest side so image fits within 250x200, preserve aspect ratio, save at resulting size (no padding). Cap is a configurable constant.
- **Dependencies**: `rapidfuzz`, `Pillow`, `requests` (pip).
- **Platforms (prototype)**: `A26` → `Atari_-_2600`, `GB` → `Nintendo_-_Game_Boy`. Map is data-driven so more can be added later.

## Repo query (user-provided)
- List titles: `https://api.github.com/repos/libretro-thumbnails/{REPO}/git/trees/master?recursive=1` — parse `tree[].path` for `^Named_Boxarts/(.+)\.png$`.
- Download image: `https://raw.githubusercontent.com/libretro-thumbnails/{REPO}/refs/heads/master/Named_Boxarts/{TITLE}.png`
- Cache the tree listing per-repo to disk (JSON) to avoid re-hitting the GitHub API (rate limit: 60 req/hr unauthenticated).

## Architecture (modules)

### 1. `config.py`
- `REPO_MAP`: `{'A26': 'Atari_-_2600', 'GB': 'Nintendo_-_Game_Boy'}`
- `ROM_EXT_MAP`: `{'A26': ['.a26', '.bin', '.zip', '.7z'], 'GB': ['.gb', '.bin', '.dmg', '.zip', '.7z']}` — only these extensions are treated as ROMs during the scan; all other files are ignored
- `MAX_SIZE = (250, 200)` — configurable cap
- `TOP_N = 10` — candidates shown per ROM
- Cache dir path (e.g., `.cache/` next to the app)

### 2. `scanner.py` — ROM scanner
- Input: root directory (user-picked via Tkinter file dialog).
- For each platform folder present under root (matched against `REPO_MAP` keys):
  - List ROM files by extension (case-insensitive), using `ROM_EXT_MAP` — only files with a recognized extension are treated as ROMs; everything else is ignored.
  - Compute cover path: `{platform_dir}/.res/{rom_basename}.png`.
  - Build work queue of ROMs **missing** a cover.
- Returns list of `RomEntry(platform, rom_path, base_name, cover_path)`.
- `rename_rom(rom_path, new_title)`: rename a local ROM file to `{new_title}{original_ext}` (preserving the extension), sanitizing the title for the filesystem. Used when the user opts to rename a ROM to match an approved candidate.

### 3. `fetcher.py` — candidate fetcher + image downloader
- `get_thumbnail_names(repo)`:
  - Check disk cache first (`.cache/{repo}.json`); if missing/stale, call the git trees API with `requests` (User-Agent header), parse `Named_Boxarts/*.png` names, write cache.
- `download_image(repo, title, dest_path)`:
  - GET the raw URL, save to temp, open with Pillow, resize to fit within `MAX_SIZE` (aspect preserved, `Image.LANCZOS`), save as PNG to `dest_path`.
  - Returns success bool.
- `fetch_preview(repo, title) -> PIL.Image` for the review window (download to temp, return image object).

### 4. `matcher.py` — title normalization + ranking
- `normalize(title)`: lowercase, strip region tags like `(USA)`, `(Europe)`, `(Japan)`, punctuation, common noise words (the, of, and, rev, proto, etc.).
- `rank_candidates(rom_name, thumbnail_names, top_n)`:
  - Score each candidate with `rapidfuzz.fuzz.token_set_ratio` (and/or `WRatio`) against the normalized ROM name.
  - Return top N as `[(score, title)]` sorted descending.
  - No hard auto-accept threshold — user decides (hybrid approach).

### 5. `gui.py` — Tkinter review window
- Layout:
  - Top: root path entry + "Scan" button; progress label (`ROM 3/42: Game Name`).
  - Middle: candidate listbox (ranked, top N) + preview `Label` showing the selected candidate's image (PIL → `ImageTk.PhotoImage`).
  - Bottom: buttons — **Accept** (save to cover path, advance), **Reject/Skip** (advance without saving), **Next/Prev** (manual navigation), **Open in viewer** (optional).
  - Output path label showing where the accepted image will be saved.
- Flow:
  1. Scan → queue.
  2. For each ROM: fetch candidates (per-platform list fetched once, cached), rank, show top N.
  3. User selects a candidate → preview updates.
  4. Accept → download + resize + save → next ROM. Reject → next ROM.
  5. End: summary (saved / skipped / failed).
- Keep it simple: one window, no tabs.

### 5b. Rename-on-approve (per-case, user opt-in)
- When the user clicks **Accept**, if the ROM's base name differs from the chosen candidate title, show a small dialog (or inline prompt) offering three choices:
  1. **Keep ROM name** — save image as `{rom_basename}.png` (default, current behavior).
  2. **Rename ROM to match image** — rename the local ROM file to `{candidate_title}{rom_ext}` (e.g., `Pac-Man.a26` → `Pac-Man (US).a26`), then save the image as `{candidate_title}.png`.
  3. **Cancel** — abort the accept, stay on this ROM.
- Renaming uses `scanner.rename_rom` (preserves the original extension, sanitizes the title, and updates the in-memory `RomEntry` so the cover path and subsequent saves use the new name).
- The rename is optional per-case; the user can always choose to keep the original ROM name.

### 6. `main.py` — entry point
- Wire modules together: scan → per-platform candidate fetch → review loop → save.
- `if __name__ == '__main__':` guard.

### 7. `requirements.txt`
- `rapidfuzz`, `Pillow`, `requests`

## Steps
1. **Scaffold** (parallel-safe): create `config.py`, `requirements.txt`, `main.py` stub.
2. **`scanner.py`** — depends on `config.py`.
3. **`fetcher.py`** — depends on `config.py` (cache dir, repo map).
4. **`matcher.py`** — standalone.
5. **`gui.py`** — depends on scanner/fetcher/matcher interfaces.
6. **`main.py`** — wire everything; depends on all.

Steps 2–4 can be built in parallel; 5–6 depend on them.

## Relevant files (all new, in `c:\Users\digil\OneDrive\Documents\python projects\rom_box_art_finder\`)
- `main.py`, `config.py`, `scanner.py`, `fetcher.py`, `matcher.py`, `gui.py`, `requirements.txt`
- Reference: `download_covers.ps1` (existing PowerShell script) — keep as reference; do not modify.

## Verification
1. `pip install -r requirements.txt` succeeds.
2. Create a test folder: `TestRoot/A26/` with a few ROMs (e.g., `Pac-Man.a26`, `Pitfall!.a26`) plus stray files (`notes.txt`, `cover.png`) → scan queues only the ROMs (extension filter works).
3. Run `python main.py`, pick `TestRoot`, scan → queue shows the ROMs.
4. For each ROM: candidates ranked sensibly (e.g., "Pitfall!" ranks near top), preview renders.
5. Accept a match → `TestRoot/A26/.res/{rom}.png` exists, dimensions ≤ 250x200, aspect preserved.
6. Accept with "Rename ROM to match image" → ROM file renamed to `{candidate_title}{ext}`, image saved as `{candidate_title}.png`, queue's cover path reflects the new name.
7. Reject a ROM → no file written, advances to next.
8. Re-scan → ROMs with existing covers are not in the queue.
9. Kill network mid-run → fetcher errors are caught, app doesn't crash.

## Scope boundaries
- **In**: A26 + GB platforms, extension-filtered ROM scan, missing-cover detection, ranked review UI, accept/reject, optional per-case ROM rename, resize + save.
- **Out (for now)**: other platforms (map is data-driven, easy to add), auto-accept thresholds, batch "accept all above score X", MAME/PS special-case filtering, zip/7z extraction.

## Further considerations
1. **Stale cache**: should the cached title list be refreshed on demand (e.g., a "Refresh list" button) or by age? Recommendation: manual refresh button + show cache age in the UI.
2. **Zip ROMs**: `rom_basename` for `Game.zip` is `Game` — matches the PS script behavior. Confirm that's desired (it is, per PS convention).
3. **Filename sanitization**: ROM names with `:` or `/` (e.g., `Game (World) [!].a26`) — sanitize to a safe filename when saving. Recommendation: replace `\\/:*?"<>|` with `_`.
