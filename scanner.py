"""ROM scanner: find ROM files missing covers and manage ROM renames."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import config


# Characters that are illegal in Windows filenames (and a few others).
_SANITIZE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """Replace filesystem-illegal characters with underscores, strip whitespace."""
    name = _SANITIZE_RE.sub("_", name)
    name = name.replace("  ", " ").strip()
    if not name:
        name = "unnamed"
    return name


@dataclass
class RomEntry:
    """A single ROM that needs a cover assigned."""

    platform: str          # e.g. 'A26'
    rom_path: Path          # absolute path to the ROM file
    res_dir: Path           # absolute path to the .res directory

    @property
    def base_name(self) -> str:
        return self.rom_path.stem

    @property
    def rom_ext(self) -> str:
        return self.rom_path.suffix.lower()

    @property
    def cover_path(self) -> Path:
        """Where the cover image for this ROM will be saved."""
        return self.res_dir / f"{sanitize_filename(self.base_name)}.png"

    @property
    def cover_exists(self) -> bool:
        return self.cover_path.exists()


# Case-insensitive lookup table: folder name (upper) -> canonical REPO_MAP key.
# Includes every REPO_MAP key and every FOLDER_ALIASES entry; alias targets
# are resolved through the table so aliases may point at other aliases.
def _build_platform_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {key.upper(): key for key in config.REPO_MAP}
    for alias, target in config.FOLDER_ALIASES.items():
        canonical = next(
            (k for k in config.REPO_MAP if k.upper() == target.upper()),
            None,
        )
        if canonical is None:
            raise ValueError(
                f"FOLDER_ALIASES entry {alias!r} points to unknown platform "
                f"{target!r}; it must exist in REPO_MAP."
            )
        lookup[alias.upper()] = canonical
    return lookup


_PLATFORM_LOOKUP = _build_platform_lookup()


def _canonical_platform(folder_name: str) -> str | None:
    """Map a folder name to its canonical REPO_MAP key, ignoring case.

    Returns ``None`` if the folder name matches no known platform.
    """
    return _PLATFORM_LOOKUP.get(folder_name.upper())


def get_roms_in_platform(platform_dir: Path, platform_key: str) -> list[Path]:
    """Return ROM files (sorted by name) in a platform folder.

    Only files whose extension appears in ``config.ROM_EXT_MAP`` are
    returned; everything else is ignored.
    """
    valid_exts = config.ROM_EXT_MAP.get(platform_key, [])
    if not valid_exts:
        return []

    roms: list[Path] = []
    for entry in platform_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() in valid_exts:
            roms.append(entry)
    return sorted(roms, key=lambda p: p.stem.lower())


def scan_platform(platform_dir: Path) -> list[RomEntry]:
    """Scan a single platform folder and return ROMs missing a cover.

    The platform key is derived from the folder name, matched
    case-insensitively against ``config.REPO_MAP`` keys and
    ``config.FOLDER_ALIASES``. Returns ROM files (by extension) that do
    not yet have a cover image in ``<platform dir>/.res/<rom>.png``.

    Raises :class:`ValueError` if the folder does not exist or its name is
    not a known platform.
    """
    if not platform_dir.is_dir():
        raise ValueError(f"Platform folder does not exist: {platform_dir}")

    # Platform key is the folder name (e.g. the chosen 'A26' folder);
    # matched against REPO_MAP case-insensitively ('snes' == 'SNES').
    platform_key = _canonical_platform(platform_dir.name)
    if platform_key is None:
        raise ValueError(
            f"'{platform_dir.name}' is not a known platform folder "
            f"(case is ignored). "
            f"Choose a folder named after a platform "
            f"(e.g. 'a26', 'snes' or an alias like 'snes02')."
        )

    res_dir = platform_dir / config.COVER_DIR_NAME
    res_dir.mkdir(exist_ok=True)

    results: list[RomEntry] = []
    for rom in get_roms_in_platform(platform_dir, platform_key):
        entry = RomEntry(
            platform=platform_key,
            rom_path=rom,
            res_dir=res_dir,
        )
        if not entry.cover_exists:
            results.append(entry)
    return results


def rename_rom(rom_entry: RomEntry, new_title: str) -> Path:
    """Rename the local ROM file to *new_title* (preserving the extension).

    Returns the new path of the ROM file. The caller is responsible for
    updating ``rom_entry.rom_path`` to the returned path.
    """
    new_name = sanitize_filename(new_title) + rom_entry.rom_ext
    new_path = rom_entry.rom_path.with_name(new_name)

    if new_path != rom_entry.rom_path:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        rom_entry.rom_path.rename(new_path)
        rom_entry.rom_path = new_path

    return new_path


def cache_key_for(repo: str) -> str:
    """Return a stable, filesystem-safe filename for a repo's title cache."""
    digest = hashlib.md5(repo.encode()).hexdigest()[:8]
    safe = re.sub(r'[^\w-]', '_', repo)
    return f"{safe}__{digest}.json"
