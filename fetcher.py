"""Candidate fetcher: lists thumbnail names from GitHub and downloads images.

- ``get_thumbnail_names`` fetches the list of ``Named_Boxarts/*.png`` titles
  for a libretro-thumbnails repo, caching the result to disk so repeated
  runs don't hit the GitHub API rate limit.
- ``download_image`` downloads a boxart, resizes it to fit within
  ``config.MAX_SIZE`` (aspect preserved), and saves it as PNG.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import requests
from PIL import Image

import config
import scanner

log = logging.getLogger(__name__)

_BOXART_RE = re.compile(r"^Named_Boxarts/(.+)\.png$")

# A raw endpoint that serves a symbolic link returns the link target path
# as plain text (optionally prefixed with Named_Boxarts/), e.g. "Outlaw.png".
_LINK_TARGET_RE = re.compile(r"^(?:Named_Boxarts/)?(.+)\.png$")


class _InFlight:
    """Coordination/result state for a single in-flight fetch of one repo."""

    __slots__ = ("done", "result", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: list[str] | None = None
        self.error: Exception | None = None


# Single-flight per repo: concurrent callers of ``get_thumbnail_names`` for the
# same repo share one fetch instead of each hitting the GitHub API.
_fetch_lock = threading.Lock()
_inflight: dict[str, _InFlight] = {}


def in_flight(repo: str) -> bool:
    """Return True while any thread is fetching title names for *repo*."""
    with _fetch_lock:
        return repo in _inflight


def _cache_file(repo: str) -> Path:
    return config.CACHE_DIR / scanner.cache_key_for(repo)


def cache_meta(repo: str) -> dict:
    """Return the fetch metadata stored in *repo*'s cache file.

    Keys: ``repo``, ``truncated`` (bool), ``fetched_at`` (ISO timestamp).
    Older cache files written before the metadata fields were introduced
    still work: missing ``truncated`` is reported as ``False`` and a missing
    ``fetched_at`` as ``None``. Returns an empty dict if there is no readable
    cache for this repo.
    """
    cache = _cache_file(repo)
    if not cache.exists():
        return {}
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read cache meta for %s: %s", repo, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "repo": data.get("repo", repo),
        "truncated": bool(data.get("truncated", False)),
        "fetched_at": data.get("fetched_at"),
    }


def get_thumbnail_names(repo: str, force_refresh: bool = False) -> list[str]:
    """Return the list of thumbnail titles for a repo.

    The list is cached on disk as JSON. Pass ``force_refresh=True`` to
    re-fetch from the API even if a cache file exists.

    Concurrent callers for the same repo share one network fetch
    (single-flight): if a fetch is already in flight, other callers wait and
    receive its result instead of issuing a second API call.
    """
    with _fetch_lock:
        entry = _inflight.get(repo)
        if entry is None:
            entry = _InFlight()
            _inflight[repo] = entry
            leader = True
        else:
            leader = False
    if not leader:
        entry.done.wait()  # bounded by the leader's API timeout
        if entry.error is not None:
            raise entry.error
        return entry.result
    try:
        titles = _fetch_thumbnail_names(repo, force_refresh)
        entry.result = titles
        return titles
    except Exception as exc:
        entry.error = exc
        raise
    finally:
        entry.done.set()
        with _fetch_lock:
            # Remove only this entry; a Refresh that started while we were
            # fetching may have replaced it with its own in-flight marker.
            if _inflight.get(repo) is entry:
                del _inflight[repo]


def _fetch_thumbnail_names(repo: str, force_refresh: bool = False) -> list[str]:
    """Perform the actual (cached or network) title-list fetch for *repo*."""
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = _cache_file(repo)

    if not force_refresh and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            names = data.get("titles", [])
            if names:
                log.info("Using cached titles for %s (%d entries)", repo, len(names))
                return names
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read cache for %s: %s", repo, exc)

    url = config.API_TREES_URL.format(repo=repo)
    headers = {"User-Agent": config.USER_AGENT}
    # An optional GITHUB_TOKEN raises the API limit from 60 to 5000 req/hr.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    # The recursive trees endpoint is heavy and GitHub occasionally returns
    # a transient 5xx; retry with backoff before giving up. (Rate limits are
    # 403; backing off would not help, but a few retries cost little and a
    # limit could reset within the window.)
    attempts = 3
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            last_exc = None
            break
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                delay = 2.0 * (attempt + 1)
                log.warning(
                    "GitHub API attempt %d/%d failed for %s (%s); retrying in %.1fs",
                    attempt + 1, attempts, repo, exc, delay,
                )
                time.sleep(delay)
    if last_exc is not None:
        raise RuntimeError(f"GitHub API request failed for {repo}: {last_exc}") from last_exc

    titles: list[str] = []
    for item in data.get("tree", []):
        m = _BOXART_RE.match(item.get("path", ""))
        if m:
            titles.append(m.group(1))

    # GitHub API truncates very large trees. Record the flag so the GUI can
    # surface it, and warn in the log.
    truncated = bool(data.get("truncated"))
    if truncated:
        log.warning(
            "GitHub API tree for %s was truncated; some titles may be missing.",
            repo,
        )

    cache.write_text(
        json.dumps(
            {
                "repo": repo,
                "titles": titles,
                "truncated": truncated,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("Fetched %d titles for %s", len(titles), repo)
    return titles


def delete_cache(repo: str) -> None:
    """Remove the cached title list for a repo (used by the Refresh button)."""
    cache = _cache_file(repo)
    if cache.exists():
        cache.unlink()


def _resize_to_fit(img: Image.Image) -> Image.Image:
    """Scale so the longest side fits within config.MAX_SIZE, aspect preserved.

    If the image already fits, it is returned unchanged.
    """
    max_w, max_h = config.MAX_SIZE
    w, h = img.size
    scale = min(max_w / w, max_h / h, 1.0)
    if scale >= 1.0:
        return img
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return img.resize((new_w, new_h), Image.LANCZOS)


class BoxartFetchError(Exception):
    """A fetch failure with a human-readable explanation."""


def _fetch_image(repo: str, title: str) -> Image.Image:
    """Download a boxart image, resolving symlinked alternate titles.

    Alternate titles (e.g. "Outlaw - Gunslinger (USA)") are symbolic links
    in the repo; raw.githubusercontent serves the link *target* as plain
    text (e.g. "Outlaw.png"). In that case we re-fetch the target image.

    Raises :class:`BoxartFetchError` on any fetch or content problem.
    """
    headers = {"User-Agent": config.USER_AGENT}

    def _get(name: str) -> requests.Response:
        url = config.RAW_IMAGE_URL.format(
            repo=repo, title=requests.utils.quote(name, safe="")
        )
        resp = requests.get(url, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp

    try:
        resp = _get(title)
        content = resp.content
        # PNG magic prefix; symlinked entries come back as text instead.
        if not content.startswith(b"\x89PNG"):
            text = content.decode("utf-8", "replace").strip()
            m = _LINK_TARGET_RE.match(text)
            if not m:
                raise BoxartFetchError(
                    f"'{title}' returned unexpected content: {text[:80]!r}"
                )
            target = m.group(1)
            log.info("%s is a symlink to %s — resolving", title, target)
            content = _get(target).content
        img = Image.open(io.BytesIO(content))
        img.load()
        return img
    except requests.RequestException as exc:
        raise BoxartFetchError(f"Failed to load '{title}': {exc}") from exc


def save_local_image(img: Image.Image, dest_path: Path) -> bool:
    """Resize *img* to fit ``config.MAX_SIZE`` and save it as PNG.

    Non-RGB/RGBA images are converted to RGBA first. Creates the parent
    directory if needed. Returns True on success, False on write error.
    Used for covers that are already in memory (the held preview image).
    """
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        img = _resize_to_fit(img)
        img.save(dest_path, "PNG")
        return True
    except OSError as exc:
        log.warning("Local save failed for %s: %s", dest_path, exc)
        return False


def download_image(repo: str, title: str, dest_path: Path) -> bool:
    """Download a boxart, resize it, and save it as PNG at *dest_path*.

    Returns True on success, False on failure (network error or bad image).
    """
    try:
        img = _fetch_image(repo, title)
    except (BoxartFetchError, OSError) as exc:
        log.warning("Download failed for %s/%s: %s", repo, title, exc)
        return False
    return save_local_image(img, dest_path)


def fetch_preview(repo: str, title: str) -> tuple[Image.Image | None, str | None]:
    """Download a boxart for preview. Returns ``(image, error)``.

    The preview is displayed in the GUI; no resizing is applied. On success
    ``image`` is a PIL.Image and ``error`` is None; on failure ``image`` is
    None and ``error`` is a human-readable explanation.
    """
    try:
        img = _fetch_image(repo, title)
        return img, None
    except (BoxartFetchError, OSError) as exc:
        log.warning("Preview fetch failed for %s/%s: %s", repo, title, exc)
        return None, str(exc)
