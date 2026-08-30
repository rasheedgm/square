"""
token_parser.py — chip-splitting primitives shared by the Path Pattern
builder: breaking one path segment (a folder or file name) into taggable
chips, and merging chips back together when a delimiter over-split
something the user wants to treat as one piece.

Every chip is delimiter-preserving: tokenize_with_separators returns the
exact separator text between each pair of chips, so the original string --
or a version of it with some chips replaced by placeholder/wildcard syntax
-- can always be rebuilt byte-for-byte (see rebuild_from_chips).
"""

import re

DEFAULT_DELIMITER_CHARS = "_.-"


def tokenize_with_separators(text, delimiter_chars=DEFAULT_DELIMITER_CHARS):
    """
    Splits text into (chips, separators) such that
    chips[0] + separators[0] + chips[1] + ... + separators[-1] + chips[-1]
    reconstructs text exactly. Any run of characters in delimiter_chars
    counts as one separator -- unlike a single fixed delimiter, this
    handles mixed conventions in the same string (e.g. both "_" and "."
    in "NAME_v003.exr") without treating the file extension as a special
    case.
    """
    if not text:
        return [], []
    char_class = re.escape(delimiter_chars)
    parts = re.split(f"([{char_class}]+)", text)
    chips = parts[0::2]
    seps = parts[1::2]
    return chips, seps


def rebuild_from_chips(chips, seps):
    """Inverse of tokenize_with_separators: rejoins chips/separators into one string."""
    if not chips:
        return ""
    out = chips[0]
    for i, sep in enumerate(seps):
        out += sep + chips[i + 1]
    return out


def merge_token_indices(chips, seps, start_idx, end_idx):
    """
    Merges chips[start_idx:end_idx+1] -- and the separators between them,
    kept verbatim inside the merged text -- into a single chip. Returns new
    (chips, seps) lists; out-of-range or empty input is returned unchanged.
    """
    if not chips or start_idx < 0 or end_idx >= len(chips) or start_idx > end_idx:
        return chips, seps
    if start_idx == end_idx:
        return chips, seps
    merged_text = chips[start_idx]
    for i in range(start_idx, end_idx):
        merged_text += seps[i] + chips[i + 1]
    new_chips = chips[:start_idx] + [merged_text] + chips[end_idx + 1:]
    new_seps = seps[:start_idx] + seps[end_idx:]
    return new_chips, new_seps
