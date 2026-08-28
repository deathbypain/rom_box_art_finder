"""Title normalization and candidate ranking.

Uses ``rapidfuzz`` to score how well a ROM name matches each available
thumbnail title. There is no automatic-accept threshold here — the ranked
list is shown to the user, who makes the final decision.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

# Region / language tags in parentheses: "(USA)", "(Europe)", "(Japan 1999)", etc.
_REGION_TAG_RE = re.compile(r"\s*\([^)]*\)")

# Brackets that often wrap region codes: "[!]", "[b-1.2]", "[plus]", etc.
_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]")

# Words that carry no identifying weight for box-art matching.
_NOISE_WORDS = frozenset({
    "the", "a", "an", "of", "and", "in", "on", "for", "with",
    "usa", "europe", "european", "japan", "japanese", "world", "ntsc", "pal",
    "rev", "revision", "en", "fr", "de", "es", "it", "proto", "prototype",
    "beta", "demo", "test", "unl", "licensed", "bootleg", "fix", "final",
})

# Region tags recognized in tie-breaking (case-insensitive): if the ROM
# carries one of these, a thumbnail that carries the same tag ranks first
# among equally-scored candidates. Detection only — they remain noise
# words for scoring.
_REGIONS = frozenset({
    "usa", "europe", "japan", "world", "france", "germany", "italy", "spain", "uk", "ntsc", "pal",
})


def _region_of(tokens: list[str]) -> str | None:
    """Return the region tag found in *tokens*, or ``None`` if no region tag."""
    for w in tokens:
        lw = w.lower()
        if lw in _REGIONS:
            return lw
    return None


# File-extension-ish tokens that sometimes appear in ROM names.
_EXT_TOKENS = frozenset({
    "a26", "nes", "sms", "gb", "gbc", "gba", "sfc", "smc", "md", "gen", "gg",
    "bin", "zip", "a78", "cue", "iso", "pbp", "chd",
})


def _split_words(name: str) -> list[str]:
    """Split a title into word tokens (alphabetic and digits only)."""
    return re.findall(r"[a-zA-Z0-9]+", name)


def _strip_noise(words: list[str], drop_single_chars: bool = True) -> list[str]:
    kept = []
    for w in words:
        lw = w.lower()
        if lw in _NOISE_WORDS or lw in _EXT_TOKENS:
            continue
        # Drop pure 1-char tokens: they are usually version/revision markers.
        if drop_single_chars and len(lw) == 1:
            continue
        kept.append(w)
    return kept


def normalize(name: str, keep_single_chars: bool = False) -> str:
    """Normalize a ROM or thumbnail title for comparison.

    Lowercases, removes region tags like ``(USA)``, bracket notes like
    ``[!]``, and strips noise words. Punctuation is reduced to spaces.

    ``keep_single_chars`` prevents the single-char-token drop, for names
    like ``B.O.B.`` whose strict normalization would otherwise vanish.
    """
    name = _REGION_TAG_RE.sub(" ", name)
    name = _BRACKET_RE.sub(" ", name)
    name = name.replace("_", " ")
    words = _split_words(name)
    words = _strip_noise(words, drop_single_chars=not keep_single_chars)
    return " ".join(w.lower() for w in words)


def rank_candidates(rom_name: str, thumbnail_names: list[str], top_n: int) -> list[tuple[int, str]]:
    """Rank thumbnail titles against a ROM name.

    Returns ``[(score, title), ...]`` sorted by score descending, limited
    to *top_n* entries with a score >= 50. Tie-breaking, in order:

    1. If the ROM carries a region tag (e.g. ``(USA)``), equally-scored
       candidates carrying the same tag rank first, so an exact regional
       match sits on top without the user having to hunt for it.
    2. The candidate whose normalized token count is closest to the ROM's
       wins — so long padded titles like
       ``Boring Journey Escape (200x) (Morgan, Charles)`` rank below clean
       ones like ``Journey Escape (USA)`` when both score equally.
    3. Fewer tokens overall, then alphabetical.
    """
    # A lenient fallback (keeps single-char tokens) for names that strict
    # normalization empties out, e.g. "B.O.B. (USA)" -> "" strictly but
    # "b o b" leniently. Both sides use it when needed so they compare
    # apples to apples.
    norm_rom = normalize(rom_name) or normalize(rom_name, keep_single_chars=True)
    if not norm_rom:
        return []
    rom_token_count = len(norm_rom.split())
    rom_region = _region_of(_split_words(rom_name))

    # (score, region_mismatch, token_distance, token_count, title)
    scored: list[tuple[int, int, int, int, str]] = []
    for title in thumbnail_names:
        norm_thumb = normalize(title) or normalize(title, keep_single_chars=True)
        if not norm_thumb:
            continue
        ratio = max(
            fuzz.token_set_ratio(norm_rom, norm_thumb),
            fuzz.token_sort_ratio(norm_rom, norm_thumb),
        )
        if ratio >= 50:  # ignore obviously unrelated titles
            thumb_token_count = len(norm_thumb.split())
            token_distance = abs(thumb_token_count - rom_token_count)
            if rom_region is None:
                region_key = 0  # no preference; preserve old ordering
            else:
                region_key = 0 if _region_of(_split_words(title)) == rom_region else 1
            scored.append((int(ratio), region_key, token_distance, thumb_token_count, title))

    scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3], item[4].lower()))
    return [(score, title) for score, *_rest, title in scored[:top_n]]
