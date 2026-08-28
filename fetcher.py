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
import re
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


def _cache_file(repo: str) -> Path:
    return config.CACHE_DIR / scanner.cache_key_for(repo)


def get_thumbnail_names(repo: str, force_refresh: bool = False) -> list[str]:
    """Return the list of thumbnail titles for a repo.

    The list is cached on disk as JSON. Pass ``force_refresh=True`` to
    re-fetch from the API even if a cache file exists.
    """
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
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"GitHub API request failed for {repo}: {exc}") from exc

    titles: list[str] = []
    for item in data.get("tree", []):
        m = _BOXART_RE.match(item.get("path", ""))
        if m:
            titles.append(m.group(1))

    # GitHub API truncates very large trees. Warn so the user can refresh.
    if data.get("truncated"):
        log.warning(
            "GitHub API tree for %s was truncated; some titles may be missing.",
            repo,
        )

    cache.write_text(
        json.dumps({"repo": repo, "titles": titles}, indent=2),
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


def download_image(repo: str, title: str, dest_path: Path) -> bool:
    """Download a boxart, resize it, and save it as PNG at *dest_path*.

    Returns True on success, False on failure (network error or bad image).
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        img = _fetch_image(repo, title)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        img = _resize_to_fit(img)
        img.save(dest_path, "PNG")
        return True
    except (BoxartFetchError, OSError) as exc:
        log.warning("Download failed for %s/%s: %s", repo, title, exc)
        return False


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
