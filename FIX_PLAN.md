# Fix Plan: ROM Box Art Finder

Created from a full code review (all Python modules, `download_covers.ps1`
reference, PyInstaller spec, `requirements.txt`, test data layout).
Work through items **in order**; each item is independently verifiable.
Status legend: `[ ]` open, `[x]` done.

---

## Phase 1 — Correctness (bugs that cause wrong behavior)

### 1. [x] Tag async tasks with their ROM index; drop stale results
**File:** `gui.py`
**Problem:** `_poll_tasks` (lines 422–492) applies background results without
checking which ROM the task was started for. Bounds check
(`0 <= self._index < len(self._queue)`) is not an identity check:
- `("candidates", …)`: pressing Next/Prev while candidates load applies the
  old ROM's candidates to the new ROM (lines 430–433).
- `("preview", …)`: no identity check at all (lines 455–474); a preview image
  can render under a different candidate's title.
- `("saved", …)`: records the outcome and advances `self._index`
  unconditionally (lines 475–484); a download started for ROM N can save/
  advance against ROM N+k. Worst case: wrong cover saved + desynced queue.
**Fix:**
- Every worker closure captures the dispatch-time index (and candidate
  identity for previews) and includes it in the queue message.
- Handlers drop tasks whose captured index/candidate no longer matches
  `self._index` / current listbox selection.
**Verify:** load candidates, then immediately press Next/Prev during the
"Loading candidates…" phase; confirm the new ROM's candidates always appear
under its own title and the old result is discarded. Repeat for previews and
accept-saves (slow network or large image).

### 2. [x] Reset queue state on idle/finish
**File:** `gui.py`
**Problem:** `_set_idle` (lines 144–154) clears the listbox/preview but not
`_queue`, `_index`, `_outcomes`. After "Queue complete" or an idle error, the
queue is silently still live: Prev resurrects the last ROM and lets you
re-accept/re-save covers.
**Fix:** add `_reset_queue_state()` clearing `_queue`, `_index`, `_outcomes`
(and `_titles_by_repo` may stay cached). Called from `_finish` and from the
idle error path. Keep the in-queue error path (status line only) separate
from the idle path.
**Implemented:** `_reset_queue_state()` is called from `_set_idle` itself —
the single choke point every idle transition goes through (`_finish`, the
idle-error `else` branch, the empty-scan path, and `__init__`). The
in-queue error path in `_set_idle_or_error` still sets only the status line
and never calls `_set_idle`, so a mid-queue error keeps the queue live.
**Verify:** complete a queue → press Prev: nothing happens. Trigger an error
while idle → re-scan works cleanly. Trigger an error mid-queue → queue stays
live, status shows the error.

### 3. [x] Dedupe ROMs sharing a cover path (PS1 `.bin`/`.cue` pairs)
**Files:** `scanner.py`, `config.py`
**Problem:** `ROM_EXT_MAP['PS1']` contains `.img`, `.bin`, `.cue`, … and
`get_roms_in_platform` returns every matching file. A standard PS1 ROM pair
(`Game.bin` + `Game.cue`) yields two queue entries with identical base name
and identical cover path — same game listed twice, cover downloaded twice.
The reference `download_covers.ps1` filtered these; the port dropped it.
**Fix:** dedupe by cover path (keep first in sorted order) in
`get_roms_in_platform` or `scan_platform`. Optionally skip `.bin`/`.img`
whose base name has a sibling `.cue` (mirrors PS-script intent) — at minimum
dedup-by-cover-path is required.
**Verify:** create `TestRoot/PS1/Game.bin` + `Game.cue`; scan lists one
entry; cover path is `Game.png`.
**Implemented:** both layers, in `get_roms_in_platform`. (1) PS1-only:
`_drop_paired_data_files` drops a `.bin` whose base name matches a sibling
`.cue` (case-insensitive; cue kept as the canonical entry — per user,
`.cue` files only ever pair with `.bin`, so `.img` is not treated as a
paired data file). (2) General backstop: after sorting by
`(stem.lower(), name.lower())`, ROMs whose *sanitized stem* (the exact key
used by `cover_path`) was already seen are dropped, so any same-base-name
pair across any platform yields one queue entry. `config.py` unchanged.
Verified: `TestRoot/PS1` with `Game.bin`+`Game.cue` (+ lone `Lone.bin`,
`CueOnly.cue`) scans to exactly `['CueOnly.cue', 'Game.cue', 'Lone.bin']`,
`Game` entry is `.cue` with cover `Game.png`.

### 4. [ ] Stop swallowing all exceptions in the task queue loop
**File:** `gui.py`
**Problem:** `except Exception: pass` (line 489) exists to catch
`queue.Empty` but also swallows handler bugs (e.g. the IndexError in item 1),
leaving a frozen UI with no message.
**Fix:** import `queue` and catch `queue.Empty` explicitly. Wrap handler
calls so a real exception is routed to `_set_idle_or_error` (or logged)
instead of vanishing.
**Verify:** force a handler error (e.g. via a deliberate unit test or a
temporarily broken task payload); confirm a message appears, not a hang.

### 5. [ ] `rename_rom` / `sanitize_filename` hardening
**Files:** `scanner.py`
**Problems:**
- `rename_rom` docstring says "The caller is responsible for updating
  `rom_entry.rom_path`" but the function already does (lines 147–155) —
  misleading contract.
- `rom_ext` lowercases the extension, so `Game.ZIP` → `game.zip` on rename;
  preserve original case.
- `sanitize_filename` doesn't handle trailing dots/spaces (Windows strips
  them; a name like `Pac-Man. ` becomes `Pac-Man ` and Windows may reject or
  silently alter the file).
**Fix:** correct the docstring; keep original extension case in
`rename_rom` (use `rom_entry.rom_path.suffix` and re-append it to the
sanitized stem rather than re-deriving); strip trailing dots/spaces in
`sanitize_filename`.
**Verify:** rename a `Game [!].ZIP` ROM → `Game [!]`, file ends `.ZIP`,
sanitize output never ends with `.` or space.

---

## Phase 2 — Concurrency & network

### 6. [ ] Lock the per-repo title cache
**Files:** `gui.py`, `fetcher.py`
**Problem:** `_titles_by_repo` is written by worker threads (lines 218–229)
while `_refresh_lists` clears it from the main thread (lines 398–417); a
Refresh + in-flight candidate load double-fetch the same repo (two GitHub
API calls; unauthenticated limit is 60 req/hr).
**Fix:**
- `threading.Lock` around `get_thumbnail_names` call + an in-flight marker so
  concurrent loads of the same repo share one fetch (worker #2 waits).
- Make Refresh wait for / cancel cleanly, or simply skip repos currently
  loading (document the behavior).
**Verify:** trigger Refresh while a candidate load is in flight; only one
API call per repo (check cache file mtime / verbose logging).

### 7. [ ] Optional `GITHUB_TOKEN` + visible truncation/rate-limit state
**Files:** `fetcher.py`, `gui.py` (or `config.py`), `config.py`
**Problems:**
- Only `User-Agent` is sent; unauthenticated API limit is 60 req/hr and
  recursive trees over ~100k entries return `truncated: true` with silently
  missing titles (only a log warning, invisible to GUI users; `fetcher.py`
  lines 71–76).
**Fix:**
- If `GITHUB_TOKEN` env var is set, send `Authorization: token <token>`
  (raises limit to 5000 req/hr).
- When `truncated` is true, surface it in the GUI (status line + cache-file
  metadata so a Refresh can tell the user). Consider storing fetch metadata
  (timestamp, truncated flag) in the cache JSON.
**Verify:** set a fake token and confirm it appears in the request (unit
test with `requests` mocked); simulate a truncated response and confirm the
GUI shows a warning, not just a log line.

### 8. [ ] Set up logging in the entry point
**Files:** `main.py`
**Problem:** `fetcher.py` uses `log.info`/`log.warning` but no handler is
configured anywhere — all of that output (including the truncation warning)
is invisible. A consoleless PyInstaller exe needs a file sink.
**Fix:** `logging.basicConfig(level=...)` in `main.py`; under `sys.frozen`,
write to `CACHE_DIR / "rom_box_art_finder.log"` (rotating, small); in dev,
to stderr.
**Verify:** run from `.venv`, see fetcher log lines; run the exe, log file
appears in `.cache`.

---

## Phase 3 — Performance

### 9. [ ] Precompute normalized thumbnail records per repo
**Files:** `matcher.py`, `gui.py` (or `fetcher.py`)
**Problem:** `rank_candidates` (matcher.py lines 88–133) re-normalizes every
~10k–60k thumbnail titles **for every ROM** (region scan + full normalize +
fuzz). Per-ROM latency on SNES/PS1 is several seconds per ROM for work that
is constant per repo.
**Fix:** build a records list once per repo:
`(title, normalized, normalized_lenient, token_count, region)` and pass it
to `rank_candidates` (or cache it keyed by `id(titles)`/repo). Ranking then
runs only fuzz ratios per ROM.
**Verify:** profile a 50-ROM SNES scan before/after; first-ROM latency
should be comparable to before (fetch cost unchanged) and subsequent ROMs
should drop to near zero per-title normalization time.

---

## Phase 4 — Build & housekeeping

### 10. [ ] Add the icon to the PyInstaller build
**Files:** `RomBoxArtFinder.spec`
**Problem:** `rom_box_art_finder_icon.png` exists in the project but the
spec has no `icon=` — the shipped `dist/RomBoxArtFinder.exe` uses the default
Python icon.
**Fix:** convert PNG → ICO (PyInstaller wants .ico on Windows) or verify
PyInstaller accepts the PNG on this setup; set `icon=` in the `EXE(...)`
call. Rebuild and run the exe from `dist\`.
**Verify:** rebuilt exe shows the icon in Explorer; app runs identically.

### 11. [ ] Pin dependencies; remove dead code
**Files:** `requirements.txt`, `gui.py`
**Problems:**
- `requirements.txt` is unpinned (rapidfuzz, Pillow, requests) — builds are
  not reproducible.
- `gui.py` lines 507–510 contain dead code (`if __name__ == "__main__":`
  block aliasing `ReviewApp`, no real entry point there — `main.py` is the
  real entry).
**Fix:** pin exact versions from the working `.venv` (also note that
`main.py` docstring mentions `.venv` launch; keep it accurate). Delete the
dead block.
**Verify:** `python -c "import gui"` still works; a clean venv from the
pinned requirements runs the app.

### 12. [ ] (Optional) Unit tests for network-free logic
**Files:** new `tests/` directory + `pytest` (dev-only dep, e.g. a separate
`requirements-dev.txt`)
**Coverage:**
- `matcher.normalize` (region tags, brackets, single-char fallback, `B.O.B.`
  case) and `rank_candidates` tie-breakers (region match, token count,
  alphabetical).
- `scanner.sanitize_filename` (illegal chars, trailing dots/spaces,
  empty → "unnamed"), platform lookup (case-insensitivity, alias
  resolution, unknown-folder error), cover-path dedupe (item 3).
- `fetcher.cache_key_for` stability and `_BOXART_RE`/`_LINK_TARGET_RE`
  parsing.
- `fetcher.get_thumbnail_names` / `download_image` with `requests` mocked
  (cache hit, cache miss, truncated flag, symlink resolution, PNG magic
  check).
**Verify:** `pytest` green; no network required.

### 13. [ ] Contain preview-pane errors (layout collapse + raw exception text)
**Discovered during item 1 manual verification (error-path test).**
**File:** `gui.py`
**Problem:** On a preview failure, the raw `fetch_preview` error string
(full URL, "Max retries exceeded", `NameResolutionError`…) is placed into the
preview-pane `tk.Label`. Long single-line text inflates the label's requested
width; when the grid's desired column widths exceed the window, Tk shrinks
columns proportionally to weight down to each column's minimum, so the
listbox column collapses to near-zero width and the preview pane swallows the
whole window. Separately, save failures only show "Download failed." with no
detail, and `_set_idle` never resets the preview label `text`
("Saving cover…"/"Loading…" text can linger after idle/finish).
**Fix:**
- Preview pane shows only short, bounded messages (e.g. "Preview download
  failed."); full details go to the status line (truncated) and the log
  (item 8). Same treatment for save-failure status: include a short reason.
- Reset the preview label `text` in `_set_idle`.
- (Optional hardening: set `wraplength` on the preview label when showing
  text so its requested width can never balloon.)
**Verify:** network off → preview failure shows a short message, window
layout stays 50/50; re-enable network and previews work again.

### 14. [x] (Optional) Reuse the previewed image on Accept (no second download)
**Implemented:** `fetcher.py` gained `save_local_image(img, dest_path)`
(resize-to-fit + PNG save, same rules as `download_image`); `download_image`
now fetches then delegates to it. `gui.py` tracks `self._preview_source`
(`(entry, candidate title)` of the held `_preview_full`), set in the preview
task handler and cleared in `_set_idle`/`_busy`/empty-candidates; a
`_preview_image_for(entry, title)` helper returns the held image only on an
exact match, and `_save_cover` saves a copy of it locally when it matches —
falling back to `download_image` otherwise. The worker gets a private
`img.copy()` so it never touches the shared preview image.
**Discovered during item 1 manual verification (Accept with network off).**
**Files:** `gui.py` (possibly `fetcher.py`)
**Problem:** `fetch_preview` downloads the full-res image for display, then
`_save_cover` downloads it a second time. With degraded/off network, Accept
fails even though the image is already in hand (reproduced manually), and
every acceptance doubles download time + raw GitHub traffic.
**Fix:** track which `(entry, candidate title)` `self._preview_full`
corresponds to; on Accept, if the current selection matches the held preview,
resize + save locally without re-downloading; otherwise fall back to the
download path.
**Verify:** preview a candidate → cut network → Accept saves the cover with
no network; a candidate without a loaded preview still downloads.

---

## Verification (overall, run after each phase)
1. `python -m pytest` (from item 12 if done) — all green, no network.
2. Manual run under `.venv` against `TestRoot/A26` + `TestRoot/snes`:
   scan → ranked candidates → preview → accept → re-scan (idempotent:
   covered ROMs not re-queued).
3. Aggressive navigation (Next/Prev during loads) — no stale/wrong results
   (items 1, 2).
4. Error path: invalid folder name, network cut mid-load — clear message,
   no hang, queue state consistent.
5. After item 10: rebuilt `dist\RomBoxArtFinder.exe` runs, shows icon,
   `.cache` + covers persist beside the exe.

## Out of scope (unchanged)
- `download_covers.ps1` stays as reference (per `plan.md`).
- No new platforms, no auto-accept, no batch acceptance.
