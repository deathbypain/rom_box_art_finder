"""Item 14 verification: Accept reuses the held preview image instead of
re-downloading it.

Headless (no network):
1. _preview_source tracks the (entry, candidate title) of the held preview;
   it is cleared by _set_idle / _busy.
2. _save_cover saves the held preview locally (resized to fit MAX_SIZE)
   when it matches the accepted candidate — no call to download_image.
3. _save_cover falls back to download_image when no preview is held
   (or it belongs to a different candidate).
4. The shared preview image is not mutated by the save (the worker gets a
   private copy).

Run:  .venv\\Scripts\\python tests\\test_item14_reuse_preview.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tkinter as tk  # noqa: E402
from PIL import Image  # noqa: E402

import config  # noqa: E402
import fetcher  # noqa: E402
import gui  # noqa: E402
import scanner  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        FAILURES.append(label)


def make_entry(name: str, res_dir: Path) -> scanner.RomEntry:
    return scanner.RomEntry(
        platform="SNES",
        rom_path=Path(name),
        res_dir=res_dir,
    )


def wait_for_saved(app: gui.ReviewApp, timeout: float = 5.0) -> None:
    """Block until a "saved" task lands in the queue, then drain it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(isinstance(t, tuple) and t and t[0] == "saved"
               for t in app._task_queue.queue):
            break
        time.sleep(0.05)
    app._poll_tasks()


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    app = gui.ReviewApp(root)
    gui.messagebox = type("M", (), {
        "showinfo": staticmethod(lambda *a, **k: None),
        "showerror": staticmethod(lambda *a, **k: None),
    })()

    res_dir = Path(__file__).resolve().parent / "_res"
    res_dir.mkdir(parents=True, exist_ok=True)
    eA = make_entry("rom_zero.sfc", res_dir)
    eB = make_entry("rom_one.sfc", res_dir)

    # Patch the network path so any fallback is detectable and cannot
    # actually touch the network.
    download_calls: list[tuple] = []
    real_download = fetcher.download_image

    def fake_download(repo: str, title: str, dest_path: Path) -> bool:
        download_calls.append((repo, title))
        Image.new("RGB", (8, 8), (0, 0, 255)).save(dest_path, "PNG")
        return True

    fetcher.download_image = fake_download

    img = Image.new("RGB", (400, 300), (255, 0, 0))  # oversized: must be scaled

    def set_current(entry: scanner.RomEntry, title: str) -> None:
        app._queue = [entry]
        app._index = 0
        app._outcomes = [None]
        app._candidates = [gui._Candidate(90, title)]
        app._listbox.delete(0, tk.END)
        app._listbox.insert(tk.END, f"90  {title}")
        app._listbox.selection_set(0)

    # ---- 1. Preview task sets _preview_source; _set_idle clears it ----
    set_current(eA, "Rom Zero Title")
    app._task_queue.put(("preview", eA, "Rom Zero Title", img, None))
    app._poll_tasks()
    check("preview applied and source tracked",
          app._preview_full is img
          and app._preview_source == (eA, "Rom Zero Title"))

    app._set_idle()
    check("_set_idle clears preview source",
          app._preview_source is None and app._preview_full is None)

    # ---- 2. Accept with matching preview: local save, no download ----
    set_current(eA, "Rom Zero Title")
    app._task_queue.put(("preview", eA, "Rom Zero Title", img, None))
    app._poll_tasks()
    check("preview held before accept", app._preview_source == (eA, "Rom Zero Title"))

    cover = eA.cover_path
    if cover.exists():
        cover.unlink()

    held = app._preview_full  # keep a reference; the worker must not mutate it
    app._save_cover(eA, app._candidates[0])
    wait_for_saved(app)

    check("no network download when preview matches",
          not download_calls)
    check("cover saved from local preview", cover.exists())
    if cover.exists():
        saved = Image.open(cover)
        max_w, max_h = config.MAX_SIZE
        check("saved cover fits MAX_SIZE (scaled, no upscale)",
              saved.size == (250, 188)  # 400x300 * 0.625
              and saved.size[0] <= max_w and saved.size[1] <= max_h)
    check("shared preview image unmutated by save",
          held is not None and held.size == (400, 300))
    check("outcome recorded as saved", app._outcomes[0] == "saved")

    # ---- 3. Accept without preview: falls back to download ----
    set_current(eB, "Rom One Title")
    # Simulate "no preview held" (e.g. preview load failed) without touching
    # the listbox/candidates:
    app._preview_source = None
    app._preview_full = None
    app._preview_photo = None
    cover_b = eB.cover_path
    if cover_b.exists():
        cover_b.unlink()

    app._save_cover(eB, app._candidates[0])
    wait_for_saved(app)

    check("download used when no preview held",
          len(download_calls) == 1
          and download_calls[0][1] == "Rom One Title")
    check("fallback cover saved", cover_b.exists())
    check("fallback outcome recorded as saved", app._outcomes[0] == "saved")

    # ---- 4. Preview for a different candidate does NOT match ----
    set_current(eA, "Rom Zero Title")
    app._task_queue.put(("preview", eA, "Rom Zero Title", img, None))
    app._poll_tasks()
    check("_preview_image_for matches held source",
          app._preview_image_for(eA, "Rom Zero Title") is img)
    check("_preview_image_for rejects other title",
          app._preview_image_for(eA, "Other Title") is None)
    check("_preview_image_for rejects other entry",
          app._preview_image_for(eB, "Rom Zero Title") is None)

    # ---- 5. _busy clears preview source (stale preview after navigation) ----
    set_current(eA, "Rom Zero Title")
    app._task_queue.put(("preview", eA, "Rom Zero Title", img, None))
    app._poll_tasks()
    app._busy("Loading...")
    check("_busy clears preview source",
          app._preview_source is None and app._preview_full is None)

    fetcher.download_image = real_download
    root.destroy()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
