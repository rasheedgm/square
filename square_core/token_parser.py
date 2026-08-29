"""
Token & Hierarchy Parser Module for Square VFX Pipeline
Handles token splitting, token merging, and 2-tier preset rule evaluations.
"""

import re
import os
from pathlib import Path


class TokenRule:
    """Defines how to split and parse a string into Sequence, Shot, Plate, Version, and Category."""

    def __init__(self, name="Custom Token Rule", delimiter="_", mapping=None, merged_ranges=None, fixed_values=None):
        self.name = name
        self.delimiter = delimiter
        # mapping dict format: { "sequence_code": [0, 1], "shot_code": [2], "plate_name": [3], "version": [4], "media_type": [5] }
        self.mapping = mapping or {}
        # list of [start_idx, end_idx] pairs that were merged
        self.merged_ranges = merged_ranges or []
        # per-role literal overrides: { "media_type": { "5": "Ref" } } -- lets a chip whose own
        # text is e.g. "PL" still resolve to a specific canonical value like "Plate", instead of
        # always using the chip's raw text for that role.
        self.fixed_values = fixed_values or {}

    def to_dict(self):
        return {
            "name": self.name,
            "delimiter": self.delimiter,
            "mapping": self.mapping,
            "merged_ranges": self.merged_ranges,
            "fixed_values": self.fixed_values,
        }

    @classmethod
    def from_dict(cls, data):
        if not data:
            return cls()
        return cls(
            name=data.get("name", "Custom Token Rule"),
            delimiter=data.get("delimiter", "_"),
            mapping=data.get("mapping", {}),
            merged_ranges=data.get("merged_ranges", []),
            fixed_values=data.get("fixed_values", {}),
        )


class HierarchyRule:
    """Defines level mapping per folder depth (Direct Tag OR Token Preset ID)."""

    def __init__(self, name="Custom Hierarchy Preset", level_mappings=None):
        self.name = name
        # level_mappings dict: { 1: {"type": "direct", "tag": "SEQ"}, 2: {"type": "token_preset", "preset_name": "Shot_Plate_Version"} }
        self.level_mappings = level_mappings or {}

    def to_dict(self):
        return {
            "name": self.name,
            "level_mappings": self.level_mappings
        }

    @classmethod
    def from_dict(cls, data):
        if not data:
            return cls()
        return cls(
            name=data.get("name", "Custom Hierarchy Preset"),
            level_mappings=data.get("level_mappings", {})
        )


def split_text_into_tokens(text, delimiter="_"):
    """Splits a string by delimiter or non-alphanumeric characters while keeping file extensions separate."""
    if not text:
        return []

    # Separate file extension if present
    base, ext = os.path.splitext(text)

    if delimiter and delimiter in base:
        tokens = [t for t in base.split(delimiter) if t]
    else:
        # Fallback split on underscores, dashes, or dots
        tokens = [t for t in re.split(r"[._\s-]+", base) if t]

    if ext:
        tokens.append(ext)

    return tokens


def merge_token_indices(tokens, start_idx, end_idx, join_char="_"):
    """Merges a slice of tokens [start_idx:end_idx+1] into a single token string."""
    if not tokens or start_idx < 0 or end_idx >= len(tokens) or start_idx > end_idx:
        return tokens

    merged = tokens[:start_idx]
    merged.append(join_char.join(tokens[start_idx:end_idx + 1]))
    merged.extend(tokens[end_idx + 1:])
    return merged


def normalise_code(raw_text, prefix_pattern, canonical_prefix, pad_width):
    """
    Recognizes a studio's own prefix convention if it's actually there (e.g.
    a chip reading "SQ010" or "seq_10") and standardizes it to a canonical
    zero-padded form. If the source has no recognizable prefix at all --
    no prefix, letters mixed in ("gfg_010_a"), a different convention
    entirely -- returns the source text unchanged. Never invents a prefix
    that wasn't there and never discards characters (letters, suffixes)
    that were.
    """
    if not raw_text:
        return raw_text
    text = raw_text.strip()
    m = re.match(rf"(?i)^(?:{prefix_pattern})[-_]?(\d+)$", text)
    if m:
        return f"{canonical_prefix}{int(m.group(1)):0{pad_width}d}"
    return text


def parse_string_with_token_rule(text, token_rule):
    """
    Applies a TokenRule to a text string.
    Returns a dict: { "sequence_code": str, "shot_code": str, "plate_name": str, "version": int, "media_type": str }
    """
    res = {
        "sequence_code": None,
        "shot_code": None,
        "plate_name": None,
        "version": None,
        "media_type": None
    }
    if not text or not token_rule:
        return res

    tokens = split_text_into_tokens(text, token_rule.delimiter)
    if not tokens:
        return res

    # Apply any stored merged ranges
    for start_idx, end_idx in token_rule.merged_ranges:
        if start_idx < len(tokens) and end_idx < len(tokens):
            tokens = merge_token_indices(tokens, start_idx, end_idx, token_rule.delimiter)

    mapping = token_rule.mapping
    fixed_values = getattr(token_rule, "fixed_values", {}) or {}

    def get_joined_token_val(indices, role=None):
        role_fixed = fixed_values.get(role, {}) if role else {}
        valid_vals = []
        for idx in indices:
            fixed = role_fixed.get(str(idx))
            if fixed is not None:
                valid_vals.append(str(fixed))
            elif 0 <= idx < len(tokens):
                valid_vals.append(tokens[idx])
        return "_".join(valid_vals) if valid_vals else None

    # 1. Sequence Code -- standardize a recognizable "SQ"/"seq" prefix if present;
    # otherwise keep the chip's own text exactly (no invented prefix, letters kept).
    if "sequence_code" in mapping and mapping["sequence_code"]:
        raw_sq = get_joined_token_val(mapping["sequence_code"], role="sequence_code")
        if raw_sq:
            res["sequence_code"] = normalise_code(raw_sq, "SQ|seq|reel|ep", "SQ", 3)

    # 2. Shot Code -- same principle: standardize a recognizable "SH"/"shot" prefix,
    # otherwise pass the chip's own text through untouched.
    if "shot_code" in mapping and mapping["shot_code"]:
        raw_sh = get_joined_token_val(mapping["shot_code"], role="shot_code")
        if raw_sh:
            res["shot_code"] = normalise_code(raw_sh, "SH|shot|sc", "SH", 4)

    # 3. Media Name
    key_name = "media_name" if "media_name" in mapping else ("plate_name" if "plate_name" in mapping else None)
    if key_name and mapping[key_name]:
        raw_pl = get_joined_token_val(mapping[key_name], role=key_name)
        if raw_pl:
            res["media_name"] = raw_pl.upper()
            res["plate_name"] = raw_pl.upper()

    # 4. Version
    if "version" in mapping and mapping["version"]:
        raw_ver = get_joined_token_val(mapping["version"], role="version")
        if raw_ver:
            digits = re.search(r"\d+", raw_ver)
            if digits:
                res["version"] = int(digits.group(0))

    # 5. Media Type -- a fixed_values override (from the "Tag as {mt}" quick menu) takes the
    # literal type name as-is; otherwise the chip's own text is matched against known types.
    if "media_type" in mapping and mapping["media_type"]:
        role_fixed = fixed_values.get("media_type", {})
        if role_fixed and any(str(i) in role_fixed for i in mapping["media_type"]):
            raw_type = get_joined_token_val(mapping["media_type"], role="media_type")
            if raw_type:
                res["media_type"] = raw_type
        else:
            raw_type = get_joined_token_val(mapping["media_type"])
            if raw_type:
                try:
                    from square_core.config import StudioConfig
                    cfg = StudioConfig()
                    canonical = next((k for k in cfg.media_type_configs.keys() if k.lower() == raw_type.lower()), None)
                    res["media_type"] = canonical if canonical else raw_type
                except Exception:
                    res["media_type"] = raw_type

    return res
