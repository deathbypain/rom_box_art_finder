"""Configuration for the ROM Box Art Finder app.

All platform mappings, ROM extension filters, and tunable constants live here.
"""

import pathlib
import sys

# ---------------------------------------------------------------------------
# Platform mapping: local folder name -> libretro-thumbnails GitHub repo name
# Add new platforms here as needed.
# ---------------------------------------------------------------------------
REPO_MAP = {
    'A26': 'Atari_-_2600',
    'GB': 'Nintendo_-_Game_Boy',
    'NES': 'Nintendo_-_Nintendo_Entertainment_System',
    'PS1': 'Sony_-_PlayStation',
    'SEGA': 'Sega_-_Mega_Drive_-_Genesis',
    'SMS': 'Sega_-_Master_System_-_Mark_III',
    'SNES': 'Nintendo_-_Super_Nintendo_Entertainment_System'
}

# ---------------------------------------------------------------------------
# ROM file extensions per platform (lowercase). Only files with one of these
# extensions are treated as ROMs during the scan; everything else is ignored.
# ---------------------------------------------------------------------------
ROM_EXT_MAP = {
    'A26': ['.a26', '.bin', '.zip', '.7z'],
    'GB': ['.gb', '.bin', '.dmg', '.zip', '.7z'],
    'NES': ['.nes', '.fds', '.zip', '.7z'],
    'PS1': ['.img', '.iso', '.bin', '.cue', '.pbp', '.chd', '.zip', '.7z'],
    'SEGA': ['.md', '.gen', '.bin', '.zip', '.7z', '.smd'],
    'SMS': ['.sms', '.zip', '.7z'],
    'SNES': ['.sfc', '.smc', '.zip', '.7z']
}

# ---------------------------------------------------------------------------
# Folder-name aliases: alternate platform folder names that should map to an
# existing REPO_MAP key, e.g. 'snes02' -> 'SNES'. Lookup is case-insensitive:
# both the folder name and the canonical key may be written in any case.
# Each alias value must be a key in REPO_MAP (validated at import).
# ---------------------------------------------------------------------------
FOLDER_ALIASES = {
    'snes02': 'SNES',
    'sfc': 'SNES',
    'ps1r': 'PS1',
    'fc': 'NES',
    'nesq': 'NES',
    'nest': 'NES',
    'md': 'SEGA'
}

# ---------------------------------------------------------------------------
# Image output: scale so the longest side fits within MAX_SIZE,
# preserving aspect ratio. Saved at the resulting (possibly smaller) size.
# ---------------------------------------------------------------------------
MAX_SIZE = (250, 200)  # (width, height)

# Number of ranked candidates to show per ROM in the review window.
TOP_N = 10

# ---------------------------------------------------------------------------
# GitHub URL templates
# ---------------------------------------------------------------------------
API_TREES_URL = (
    "https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master"
    "?recursive=1"
)
RAW_IMAGE_URL = (
    "https://raw.githubusercontent.com/libretro-thumbnails/{repo}"
    "/refs/heads/master/Named_Boxarts/{title}.png"
)
USER_AGENT = "RomBoxArtFinder/1.0"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Under PyInstaller, __file__ points into a temp extraction folder that is
# recreated on every launch. Anchor instead to:
#   - one-dir builds: the dist folder containing the exe (sys.executable)
#   - one-file builds: also next to the exe, so the .cache lives persistently
#     beside RomBoxArtFinder.exe
if getattr(sys, "frozen", False):  # PyInstaller (or Nuitka) build
    APP_DIR = pathlib.Path(sys.executable).resolve().parent
else:  # running from source
    APP_DIR = pathlib.Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / ".cache"

# Directory (relative to each platform folder) where cover images are saved.
COVER_DIR_NAME = ".res"
