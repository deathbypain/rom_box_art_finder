"""Item 1 verification: async tasks carry ROM identity; stale results are dropped.

Headless (no network, no disk writes):
1. Dispatch tagging: _show_current puts a ("candidates", <entry>, ...) task
   whose entry is the ROM it was started for.
2. Stale drop: a candidates/preview/saved/error task for an entry that is no
   longer the current ROM is discarded, not applied.
3. Selection drop: a preview task whose title no longer matches the selected
   candidate is discarded even if the entry is current.

Run:  .venv\\Scripts\\python tests\\test_item1_stale_drop.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tkinter as tk  # noqa: E402
from PIL import Image  # noqa: E402

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


def main() -> None:
    # Withdrawn Tk window: real widgets, never shown.
    root = tk.Tk()
    root.withdraw()
    app = gui.ReviewApp(root)
    # Suppress any dialogs the app might try to show.
    gui.messagebox = type("M", (), {
        "showinfo": staticmethod(lambda *a, **k: None),
        "showerror": staticmethod(lambda *a, **k: None),
    })()

    res_dir = Path(__file__).resolve().parent / "_res"
    eA = make_entry("rom_zero.sfc", res_dir)
    eB = make_entry("rom_one.sfc", res_dir)

    # ---- 1. Dispatch tagging: _show_current tags the task with its entry ----
    app._titles_by_repo = {
        "Nintendo_-_Super_Nintendo_Entertainment_System":
            ["Rom Zero Title", "Rom One Title"],
    }
    record: list[tuple] = []
    real_put = app._task_queue.put
    app._task_queue.put = lambda t: (record.append(t), real_put(t))

    app._queue = [eA, eB]
    app._index = 0
    app._outcomes = [None, None]
    app._show_current()  # starts a worker thread

    deadline = time.time() + 5
    while not any(isinstance(t, tuple) and t and t[0] == "candidates"
                  for t in record) and time.time() < deadline:
        time.sleep(0.05)
    app._task_queue.put = real_put

    cand_tasks = [t for t in record if isinstance(t, tuple) and t and t[0] == "candidates"]
    check("dispatch tagged candidates with the ROM entry",
          len(cand_tasks) == 1 and cand_tasks[0][1] is eA)

    # ---- 2. Stale drop: apply a task for eA while eB is current ----
    app._task_queue.empty()  # clear the dispatched (eA) task
    app._queue = [eA, eB]
    app._index = 1
    app._outcomes = [None, None]
    app._candidates = [gui._Candidate(90, "Rom One Title")]
    app._listbox.delete(0, tk.END)
    app._listbox.insert(tk.END, "90  Rom One Title")
    app._listbox.selection_set(0)

    img = Image.new("RGB", (4, 4), (255, 0, 0))
    app._task_queue.put(("candidates", eA, [(99, "Rom Zero Title")]))  # stale entry
    app._task_queue.put(("preview", eA, "Rom Zero Title", img, None))  # stale entry
    app._task_queue.put(("saved", eA, True, True))                     # stale entry
    app._task_queue.put(("error", eA, "boom"))                         # stale entry
    app._poll_tasks()

    check("stale candidates dropped (listbox unchanged)",
          app._listbox.get(0) == "90  Rom One Title"
          and app._candidates[0].title == "Rom One Title")
    check("stale saved dropped (index unchanged)",
          app._index == 1)
    check("stale error dropped (status unchanged)",
          app._status_var.get() != "Error: boom")

    # ---- 3. Fresh task for the current entry IS applied ----
    app._task_queue.put(("candidates", eB, [(98, "Rom One Title")]))
    app._task_queue.put(("preview", eB, "Rom One Title", img, None))
    app._poll_tasks()
    check("fresh candidates applied for current entry",
          app._candidates[0].title == "Rom One Title"
          and app._listbox.get(0).endswith("Rom One Title"))
    check("fresh preview rendered for current entry",
          app._preview_full is img)

    # ---- 4. Selection drop: right entry, but a different candidate chosen ----
    app._preview_full = None
    app._preview_photo = None
    app._task_queue.put(("preview", eB, "Not Selected Title", img, None))
    app._poll_tasks()
    check("preview for non-selected candidate dropped",
          app._preview_full is None)

    # ---- 5. Saved for the current entry applies and advances ----
    app._task_queue.put(("saved", eB, True, True))
    app._task_queue.put(("candidates", eA, [(1, "junk")]))  # stale again
    app._poll_tasks()
    check("saved applied for current entry (index advanced)",
          app._index == 2 and app._outcomes[1] == "saved")

    # ---- 6. Error with entry=None (scan/refresh) is always shown ----
    app._queue = [eA, eB]
    app._index = 1
    app._task_queue.put(("error", None, "global failure"))
    app._poll_tasks()
    check("unscoped error surfaced",
          app._status_var.get() == "Error: global failure")

    # ---- 7. Error scoped to an abandoned entry is dropped ----
    app._status_var.set("")
    app._task_queue.put(("error", eA, "stale failure"))
    app._poll_tasks()
    check("stale scoped error dropped",
          app._status_var.get() != "Error: stale failure")

    root.destroy()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
