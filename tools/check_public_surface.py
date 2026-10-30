#!/usr/bin/env python3
"""Fail if user-facing files still show retired sample branding.

Run from anywhere:

    python3 tools/check_public_surface.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from public_ids import (
    DISCLAIMER,
    LEGACY_TO_PUBLIC,
    PUBLIC_IMPORT_SYMBOL,
    PUBLIC_PACK_SYMBOL,
    canonical_public_id,
    public_disclaimer,
)

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", "__pycache__"}
SKIP_FILES = {
    Path("tools/public_ids.py"),
    Path("tools/check_public_surface.py"),
}
RETIRED_SURFACE = re.compile(r"globex|\bNQ\b|\bES\b|\bGC\b", re.IGNORECASE)

# Expected loader map. Duplicated on purpose so an edit to the alias table fails here.
EXPECTED_ALIASES = {
    "SYNTH-NQ-GLOBEX": "SYNTH-TECHIDX-24H",
    "PRACTICE-NQ-GLOBEX": "PRACTICE-TECHIDX-24H",
    "SYNTH-NQ": "SYNTH-IDX1",
    "SYNTH-ES": "SYNTH-IDX2",
    "SYNTH-GC": "SYNTH-METAL1",
}
UNCHANGED = ("SYNTH-BTC", "SYNTH-IDX1", "SYNTH-IDX2", "SYNTH-METAL1", "SYNTH-TECHIDX-24H", "PRACTICE-TECHIDX-24H")

REQUIRED_PHRASES = (
    "not index-licensed",
    "not affiliated",
    "CME",
    "Nasdaq",
    "synthetic practice",
    "near-24h session-like",
    PUBLIC_PACK_SYMBOL,
    PUBLIC_IMPORT_SYMBOL,
)


def iter_public_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_PARTS for part in rel.parts):
            continue
        if rel in SKIP_FILES:
            continue
        yield rel, path


def check_aliases(errors: list[str]) -> None:
    if LEGACY_TO_PUBLIC != EXPECTED_ALIASES:
        errors.append("LEGACY_TO_PUBLIC does not match the expected alias map")
    for old, new in EXPECTED_ALIASES.items():
        got = canonical_public_id(old)
        if got != new:
            errors.append(f"alias {old} -> {got}, expected {new}")
    for symbol in UNCHANGED:
        if canonical_public_id(symbol) != symbol:
            errors.append(f"public id {symbol} was rewritten")
    if "SYNTH-BTC" in LEGACY_TO_PUBLIC:
        errors.append("SYNTH-BTC must stay unchanged and out of the alias map")
    try:
        canonical_public_id("custom-globex-pack")
    except ValueError:
        pass
    else:
        errors.append("unknown retired branding was accepted")
    legacy = "Not CME, NQ, or any vendor replay. Globex-like calendar."
    if public_disclaimer(legacy) != DISCLAIMER:
        errors.append("retired disclaimer was not replaced")
    if public_disclaimer(DISCLAIMER) != DISCLAIMER:
        errors.append("canonical disclaimer was rewritten")
    if public_disclaimer(None) != DISCLAIMER:
        errors.append("missing disclaimer did not fall back to the canonical text")
    if RETIRED_SURFACE.search(DISCLAIMER):
        errors.append("canonical disclaimer still matches retired branding")


def check_published_json(errors: list[str]) -> None:
    packs = (
        Path("SYNTH-TECHIDX-24H_meta.json"),
        Path("samples/SYNTH-TECHIDX-24H_14d_meta.json"),
        Path("params-hand-tuned.json"),
        Path("params-hand-tuned-14d.json"),
    )
    for rel in packs:
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        if data.get("symbol") != PUBLIC_PACK_SYMBOL:
            errors.append(f"{rel} symbol is {data.get('symbol')!r}")
        if data.get("disclaimer") != DISCLAIMER:
            errors.append(f"{rel} disclaimer drifted from the canonical text")
        generator = data.get("generator")
        if not isinstance(generator, dict):
            continue
        if generator.get("name") != "kinfx-techidx-24h-synth":
            errors.append(f"{rel} generator name is {generator.get('name')!r}")
        if generator.get("calendar") != "near-24h-session-like-est-fixed":
            errors.append(f"{rel} calendar id is {generator.get('calendar')!r}")


def check_disclaimer_files(errors: list[str]) -> None:
    for name in ("README.md", "NOTICE"):
        text = (ROOT / name).read_text(encoding="utf-8")
        lowered = text.lower()
        for phrase in REQUIRED_PHRASES:
            if phrase.lower() not in lowered:
                errors.append(f"{name} missing {phrase!r}")


def check_tree(errors: list[str]) -> None:
    for rel, path in iter_public_files():
        if RETIRED_SURFACE.search(rel.as_posix()):
            errors.append(f"path {rel}")
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            if RETIRED_SURFACE.search(line):
                errors.append(f"{rel}:{line_no}: {line.strip()[:180]}")


def main() -> int:
    errors: list[str] = []
    check_aliases(errors)
    check_disclaimer_files(errors)
    check_published_json(errors)
    check_tree(errors)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
