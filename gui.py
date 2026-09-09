"""Tkinter review window for the ROM Box Art Finder.

One window: path entry, progress, ranked candidate list, preview image,
and Accept / Reject / nav controls. Network work (title lists, previews,
downloads) runs in background threads; results are marshalled back to the
Tkinter main thread via a queue.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from queue import Queue as ThreadQueue
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

import config
import fetcher
import matcher
import scanner




class _Candidate:
    """One ranked candidate title (score is a 0-100 rapidfuzz ratio)."""

    def __init__(self, score: int, title: str):
        self.score = score
        self.title = title


class ReviewApp:
    """Main application window."""

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("ROM Box Art Finder")
        root.geometry("760x560")

        self._task_queue: ThreadQueue = ThreadQueue()
        self._queue: list[scanner.RomEntry] = []
        self._index = 0
        # One outcome per queued ROM; stats are derived from this list so
        # revisiting a ROM (e.g. accepting after a reject) can flip its state.
        self._outcomes: list[str | None] = []
        self._titles_by_repo: dict[str, list[str]] = {}
        self._candidates: list[_Candidate] = []
        # Full-resolution preview image (kept so we can re-fit on resize).
        self._preview_full: Image.Image | None = None
        self._preview_photo: ImageTk.PhotoImage | None = None  # keep a ref alive
        # (entry, candidate title) of the image in _preview_full, if any —
        # lets Accept save that image locally instead of re-downloading it.
        self._preview_source: tuple[scanner.RomEntry, str] | None = None

        self._build_widgets()
        self._set_idle()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------
    def _build_widgets(self) -> None:
        # Bottom rows are docked to the bottom edge FIRST, so they always
        # keep their room and the resizable body shrinks first on small windows.
        bottom = ttk.Frame(self.root, padding=(8, 4))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        status = ttk.Frame(self.root, padding=8)
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self._status_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self._status_var).pack(side=tk.LEFT)

        self._dest_var = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self._dest_var, anchor=tk.W,
                  padding=(12, 0), foreground="#444444").pack(
            side=tk.BOTTOM, fill=tk.X)

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        self._path_var = tk.StringVar()
        ttk.Label(top, text="Platform folder:").pack(side=tk.LEFT)
        self._path_entry = ttk.Entry(top, textvariable=self._path_var)
        self._path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(top, text="Browse...", command=self._browse).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top, text="Scan", command=self._scan).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(top, text="Refresh list", command=self._refresh_lists).pack(side=tk.LEFT, padx=(4, 0))

        mismatch = ttk.Frame(self.root, padding=(8, 0))
        mismatch.pack(fill=tk.X)
        ttk.Label(mismatch, text="If ROM name differs from image name:").pack(
            side=tk.LEFT)
        self._rename_policy = tk.StringVar()
        self._rename_policy.set("Keep ROM name")
        ttk.OptionMenu(
            mismatch, self._rename_policy,
            "Keep ROM name",
            "Keep ROM name", "Rename ROM to match image",
        ).pack(side=tk.LEFT, padx=(4, 0))

        self._progress_var = tk.StringVar(value="No queue. Choose a platform folder and press Scan.")
        ttk.Label(self.root, textvariable=self._progress_var,
                  anchor=tk.W).pack(fill=tk.X, padx=8)

        body = ttk.Frame(self.root, padding=(8, 4))
        body.pack(fill=tk.BOTH, expand=True)
        # 50/50 split between the candidate list and the preview, and the
        # grid grows to fill whatever space the window offers.
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._listbox = tk.Listbox(body, exportselection=False)
        self._listbox.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self._listbox.bind("<<ListboxSelect>>", self._on_candidate_select)

        preview_box = ttk.Frame(body)
        preview_box.grid(row=0, column=1, sticky="nsew")
        self._preview_label = tk.Label(preview_box, relief=tk.SUNKEN, borderwidth=1)
        self._preview_label.pack(fill=tk.BOTH, expand=True)
        # Re-fit the preview when the pane resizes so it always fills the space.
        self._preview_label.bind("<Configure>", self._on_preview_resize)

        self._title_var = tk.StringVar(value="(no candidate selected)")
        ttk.Label(body, textvariable=self._title_var, anchor=tk.CENTER,
                  padding=(8, 4, 0, 0)).grid(row=1, column=1, sticky="ew")

        ttk.Button(bottom, text="Accept", command=self._accept).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Reject / Skip", command=self._reject).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(bottom, text="Next", command=lambda: self._step(1)).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="Prev", command=lambda: self._step(-1)).pack(
            side=tk.RIGHT, padx=(4, 0))

        # Keyboard shortcuts: A = accept, R = reject/skip, P = prev, N = next.
        # Bound to the window, so they work without Alt as long as the path
        # entry doesn't have focus (where the keys must type normally).
        self.root.bind("a", self._shortcut_accept)
        self.root.bind("r", self._shortcut_reject)
        self.root.bind("p", self._shortcut_prev)
        self.root.bind("n", self._shortcut_next)

    # -----------------------------------------------------------------------
    # Idle / state helpers
    # -----------------------------------------------------------------------
    def _reset_queue_state(self) -> None:
        """Clear the live queue so an idle/finished app can't resurrect ROMs.

        _set_idle clears the listbox and preview; this clears the queue
        bookkeeping (_queue / _index / _outcomes) that _step, _accept,
        _reject and _show_current still read. Without it, Prev after
        "Queue complete" revives the last ROM and lets the user re-accept /
        re-save its cover. _titles_by_repo is deliberately left cached.
        """
        self._queue = []
        self._index = 0
        self._outcomes = []

    def _set_idle(self) -> None:
        """Show no-queue state (and make sure the queue is not live)."""
        self._reset_queue_state()
        self._listbox.delete(0, tk.END)
        self._candidates = []
        self._preview_full = None
        self._preview_photo = None
        self._preview_source = None
        self._preview_label.config(image="")
        self._title_var.set("No candidates loaded.")
        self._dest_var.set("")
        self._progress_var.set("No queue. Choose a platform folder and press Scan.")
        self._status_var.set("")

    def _busy(self, message: str) -> None:
        self._progress_var.set(message)
        # Drop the current preview so stale art doesn't linger while loading.
        self._preview_full = None
        self._preview_photo = None
        self._preview_source = None
        self._preview_label.config(text="Loading...", image="")

    def _is_current_entry(self, entry: scanner.RomEntry) -> bool:
        """True if *entry* is the ROM currently shown (queue live, at _index).

        Every async task carries the entry it was started for; handlers use
        this identity check to drop results that arrived after the user
        navigated away (or after a new scan replaced the queue).
        """
        return (
            0 <= self._index < len(self._queue)
            and self._queue[self._index] is entry
        )

    def _selection_is(self, title: str) -> bool:
        """True if the currently selected candidate has the given title."""
        sel = self._listbox.curselection()
        return bool(sel) and self._candidates[sel[0]].title == title

    def _preview_image_for(self, entry: scanner.RomEntry, title: str) -> Image.Image | None:
        """The full-res preview image, if it is the one for *entry*/*title*."""
        source = self._preview_source
        if source is None:
            return None
        src_entry, src_title = source
        if src_entry is entry and src_title == title:
            return self._preview_full
        return None

    # -----------------------------------------------------------------------
    # Scanning
    # -----------------------------------------------------------------------
    def _browse(self) -> None:
        path = filedialog.askdirectory(title="Choose the platform folder")
        if path:
            self._path_var.set(path)

    def _scan(self) -> None:
        platform_dir = Path(self._path_var.get())
        if not platform_dir.is_dir():
            messagebox.showerror("Scan", f"Platform folder does not exist:\n{platform_dir}")
            return

        self._busy("Scanning...")
        self.root.update_idletasks()

        def _work() -> None:
            try:
                entries = scanner.scan_platform(platform_dir)
                self._task_queue.put(("scanned", entries))
            except Exception as exc:  # surface errors on the UI thread
                self._task_queue.put(("error", None, str(exc)))

        threading.Thread(target=_work, daemon=True).start()

    def _start_queue(self, entries: list[scanner.RomEntry]) -> None:
        if not entries:
            self._set_idle()
            messagebox.showinfo("Scan", "No ROMs are missing covers in this platform folder.")
            return
        self._queue = entries
        self._index = 0
        self._outcomes = [None] * len(entries)
        self._show_current()

    def _show_current(self) -> None:
        """Load candidates for queue[_index] and display them."""
        if not (0 <= self._index < len(self._queue)):
            self._finish()
            return
        entry = self._queue[self._index]
        repo = config.REPO_MAP[entry.platform]
        self._candidates = []
        self._listbox.delete(0, tk.END)
        self._preview_photo = None
        self._preview_label.config(text="Select a candidate to preview.", image="")
        self._title_var.set(entry.base_name)
        self._dest_var.set(f"Output: {entry.cover_path}")
        stats = self._stats()
        self._progress_var.set(
            f"ROM {self._index + 1}/{len(self._queue)}: {entry.base_name}  "
            f"(saved {stats['saved']} / skipped {stats['skipped']})"
        )

        def _work() -> None:
            try:
                if repo not in self._titles_by_repo:
                    self._titles_by_repo[repo] = fetcher.get_thumbnail_names(repo)
                titles = self._titles_by_repo[repo]
                ranked = matcher.rank_candidates(entry.base_name, titles, config.TOP_N)
                self._task_queue.put(
                    ("candidates", entry, [(s, t) for s, t in ranked])
                )
            except Exception as exc:
                self._task_queue.put(("error", entry, str(exc)))

        self._busy(f"Loading candidates for: {entry.base_name}")
        threading.Thread(target=_work, daemon=True).start()

    def _load_candidate_preview(self, candidate: _Candidate) -> None:
        """Download a candidate's boxart for the preview pane (thread-safe)."""
        entry = self._queue[self._index]
        repo = config.REPO_MAP[entry.platform]
        self._title_var.set(f"{candidate.title}  (score {candidate.score})")
        self._busy(f"Previewing: {candidate.title}")

        def _work() -> None:
            img, error = fetcher.fetch_preview(repo, candidate.title)
            self._task_queue.put(
                ("preview", entry, candidate.title, img, error)
            )

        threading.Thread(target=_work, daemon=True).start()

    # -----------------------------------------------------------------------
    # Preview rendering (aspect-preserving, fits the pane)
    # -----------------------------------------------------------------------
    def _on_preview_resize(self, _event: tk.Event) -> None:
        """Re-fit the preview whenever the pane is resized."""
        self._render_preview()

    def _render_preview(self) -> None:
        """Draw the full image into the pane, preserving aspect ratio.

        Scales down to fit the pane (never upscales), so on a short window
        the image shrinks to fit instead of being clipped or leaving a big
        blank box.
        """
        img = self._preview_full
        if img is None:
            return
        label = self._preview_label
        w, h = label.winfo_width(), label.winfo_height()
        # Leave a few px for the sunken border so it doesn't clip.
        border = label.cget("borderwidth")
        max_w = max(64, w - 2 * (border + 1))
        max_h = max(64, h - 2 * (border + 1))

        iw, ih = img.size
        scale = min(max_w / iw, max_h / ih)
        if scale >= 1:  # never upscale; show original size
            scale = 1
        new_w = max(1, round(iw * scale))
        new_h = max(1, round(ih * scale))
        # Avoid re-rendering if the size is unchanged (e.g. repeated events).
        if self._preview_photo is not None and (new_w, new_h) == (
            self._preview_photo.width(),
            self._preview_photo.height(),
        ):
            return
        if scale < 1:
            img = img.copy()
            img = img.resize((new_w, new_h), Image.LANCZOS)
        self._preview_photo = ImageTk.PhotoImage(img)
        label.config(image=self._preview_photo, text="")

    # -----------------------------------------------------------------------
    # Candidate list interaction
    # -----------------------------------------------------------------------
    def _on_candidate_select(self, _event: tk.Event) -> None:
        sel = self._listbox.curselection()
        if not sel:
            return
        self._load_candidate_preview(self._candidates[sel[0]])

    def _step(self, delta: int) -> None:
        new_index = self._index + delta
        if 0 <= new_index < len(self._queue):
            self._index = new_index
            self._show_current()

    def _reject(self) -> None:
        if not self._queue:
            return
        self._outcomes[self._index] = "rejected"
        self._index += 1
        self._show_current()

    # -----------------------------------------------------------------------
    # Keyboard shortcuts (A / R / P / N, no Alt required)
    # -----------------------------------------------------------------------
    def _focus_is_typing(self) -> bool:
        """True when keyboard focus is in the path entry (keys must type normally)."""
        return self.root.focus_get() is self._path_entry

    def _shortcut_accept(self, _event: tk.Event) -> str | None:
        if self._focus_is_typing():
            return
        self._accept()
        return "break"

    def _shortcut_reject(self, _event: tk.Event) -> str | None:
        if self._focus_is_typing():
            return
        self._reject()
        return "break"

    def _shortcut_prev(self, _event: tk.Event) -> str | None:
        if self._focus_is_typing():
            return
        self._step(-1)
        return "break"

    def _shortcut_next(self, _event: tk.Event) -> str | None:
        if self._focus_is_typing():
            return
        self._step(1)
        return "break"


    # -----------------------------------------------------------------------
    # Accept flow (with optional ROM rename)
    # -----------------------------------------------------------------------
    def _accept(self) -> None:
        sel = self._listbox.curselection()
        if not sel or not self._queue:
            messagebox.showinfo("Accept", "Select a candidate to accept.")
            return
        entry = self._queue[self._index]
        candidate = self._candidates[sel[0]]

        rom_title_matches = candidate.title.lower() == entry.base_name.lower()
        if not rom_title_matches and self._rename_policy.get() == "Rename ROM to match image":
            try:
                scanner.rename_rom(entry, candidate.title)
            except OSError as exc:
                messagebox.showerror("Rename failed", str(exc))
                return
            # entry.rom_path is updated in place; entry.cover_path
            # recomputes from the new base_name automatically.

        self._save_cover(entry, candidate)

    def _save_cover(self, entry: scanner.RomEntry, candidate: _Candidate) -> None:
        """Save the selected cover, then advance.

        If the preview pane already holds this entry/candidate's image, it is
        saved locally (resized to fit) instead of being downloaded a second
        time; otherwise the cover is downloaded as before.
        """
        repo = config.REPO_MAP[entry.platform]
        # Copy on the UI thread so the worker owns a private image and never
        # touches the shared _preview_full while Tk is rendering it.
        preview = self._preview_image_for(entry, candidate.title)
        local_img = preview.copy() if preview is not None else None
        self._busy(f"Saving cover: {candidate.title}")
        self.root.update_idletasks()

        def _work() -> None:
            if local_img is not None:
                ok = fetcher.save_local_image(local_img, entry.cover_path)
            else:
                ok = fetcher.download_image(repo, candidate.title, entry.cover_path)
            self._task_queue.put(("saved", entry, ok, entry.cover_path.exists()))

        threading.Thread(target=_work, daemon=True).start()

    def _stats(self) -> dict[str, int]:
        """Counts derived from the per-queue ROM outcomes."""
        return {
            "saved": self._outcomes.count("saved"),
            "skipped": self._outcomes.count("rejected"),
            "failed": self._outcomes.count("failed"),
        }

    def _finish(self) -> None:
        s = self._stats()
        self._set_idle()
        self._progress_var.set("Queue complete.")
        messagebox.showinfo(
            "Done",
            "Review complete.\n\n"
            f"Saved:   {s['saved']}\n"
            f"Skipped: {s['skipped']}\n"
            f"Failed:  {s['failed']}",
        )

    # -----------------------------------------------------------------------
    # Refresh (title-list cache)
    # -----------------------------------------------------------------------
    def _refresh_lists(self) -> None:
        """Re-fetch title lists from the API (clears disk cache for used repos).

        For the prototype this clears the whole app memory cache and deletes
        disk cache for every configured repo, then re-fetches on demand.

        A repo that a candidate load is *already* fetching is skipped: the
        single-flight guard in ``fetcher`` means it will finish fetching and
        repopulate the cache shortly, so double-fetching it here would just
        burn rate limit. (Documented behavior: Refresh + in-flight candidate
        load on the same repo issues one API call, not two.)
        """
        self._titles_by_repo.clear()

        def _work() -> None:
            try:
                for repo in config.REPO_MAP.values():
                    if fetcher.in_flight(repo):
                        continue  # a candidate load owns this fetch; let it finish
                    fetcher.delete_cache(repo)
                    fetcher.get_thumbnail_names(repo)
                self._task_queue.put(("refreshed", 0))
            except Exception as exc:
                self._task_queue.put(("error", None, str(exc)))

        self._busy("Refreshing title lists...")
        self.root.update_idletasks()
        threading.Thread(target=_work, daemon=True).start()

    # -----------------------------------------------------------------------
    # Async result polling
    # -----------------------------------------------------------------------
    def _poll_tasks(self) -> None:
        try:
            while True:
                try:
                    task = self._task_queue.get_nowait()
                except queue.Empty:
                    break  # nothing queued yet; wait for the next poll
                kind = task[0]
                if kind == "scanned":
                    self._start_queue(task[1])
                elif kind == "candidates":
                    entry, ranked = task[1], task[2]
                    if not self._is_current_entry(entry):
                        continue  # user navigated away while this was loading
                    self._candidates = [_Candidate(s, t) for s, t in ranked]
                    self._listbox.delete(0, tk.END)
                    for c in self._candidates:
                        self._listbox.insert(tk.END, f"{c.score:3d}  {c.title}")
                    if self._candidates:
                        self._listbox.selection_set(0)
                        self.root.after(0, lambda: self._on_candidate_select(None))
                        # Keyboard belongs to the candidate list for this ROM
                        # (arrows + the A/R/P/N shortcuts), regardless of what
                        # had focus before.
                        self._listbox.focus_force()
                        entry = self._queue[self._index]
                        stats = self._stats()
                        self._progress_var.set(
                            f"ROM {self._index + 1}/{len(self._queue)}: {entry.base_name}  "
                            f"(saved {stats['saved']} / skipped {stats['skipped']})"
                        )
                    else:
                        self._title_var.set("No candidates matched.")
                        self._preview_full = None
                        self._preview_photo = None
                        self._preview_source = None
                        self._preview_label.config(text="No candidates matched.", image="")
                    # Surface truncation warning (list may be incomplete).
                    repo = config.REPO_MAP[entry.platform]
                    meta = fetcher.cache_meta(repo)
                    if meta.get("truncated"):
                        self._status_var.set(
                            f"\u26a0 Truncated list for {entry.platform} \u2013 some titles may be missing."
                        )
                elif kind == "preview":
                    entry, title, img, error = task[1], task[2], task[3], task[4]
                    if not (
                        self._is_current_entry(entry)
                        and self._selection_is(title)
                    ):
                        continue  # user moved on or picked a different candidate
                    if img is None:
                        self._preview_full = None
                        self._preview_photo = None
                        self._preview_source = None
                        self._preview_label.config(
                            text=error or "Preview download failed.", image=""
                        )
                    else:
                        self._preview_full = img
                        self._preview_source = (entry, title)
                        # Invalidate the previous render so this fresh image
                        # is drawn even if it lands on the same pixel size.
                        self._preview_photo = None
                        self._render_preview()
                    stats = self._stats()
                    entry = self._queue[self._index]
                    self._progress_var.set(
                        f"ROM {self._index + 1}/{len(self._queue)}: {entry.base_name}  "
                        f"(saved {stats['saved']} / skipped {stats['skipped']})"
                    )
                elif kind == "saved":
                    entry, ok, _ = task[1], task[2], task[3]
                    if not self._is_current_entry(entry):
                        continue  # queue advanced or replaced while saving
                    self._outcomes[self._index] = "saved" if ok else "failed"
                    if ok:
                        self._status_var.set(f"Saved: {entry.cover_path.name}")
                    else:
                        self._status_var.set("Download failed.")
                    self._index += 1
                    self._show_current()
                elif kind == "refreshed":
                    self._progress_var.set("Title lists refreshed.")
                    # Tell the user if any repo's list came back truncated.
                    # Show the affected platform names (friendlier than URLs).
                    truncated_platforms = [
                        platform
                        for platform, repo in config.REPO_MAP.items()
                        if fetcher.cache_meta(repo).get("truncated")
                    ]
                    if truncated_platforms:
                        self._status_var.set(
                            "\u26a0 Truncated list(s) for "
                            + ", ".join(sorted(truncated_platforms))
                            + " \u2013 some titles may be missing."
                        )
                elif kind == "error":
                    entry, message = task[1], task[2]
                    if entry is not None and not self._is_current_entry(entry):
                        continue  # error belongs to a ROM the user left
                    self._set_idle_or_error(message)
        except Exception as exc:
            # A task handler raised (e.g. the item 1 IndexError). Surface it
            # instead of freezing silently; any still-queued tasks are
            # re-polled on the next tick.
            self._set_idle_or_error(f"Task handling failed: {exc}")

        self.root.after(100, self._poll_tasks)

    def _set_idle_or_error(self, message: str) -> None:
        if self._queue and self._index < len(self._queue):
            self._status_var.set(f"Error: {message}")
        else:
            self._set_idle()
            messagebox.showerror("Error", message)

    def run(self) -> None:
        """Start polling and enter the Tkinter main loop."""
        self.root.after(100, self._poll_tasks)
        self.root.mainloop()


if __name__ == "__main__":
    import tkinter  # noqa: F401  (fail fast if Tk is missing)

    app_review = ReviewApp  # convenience alias
