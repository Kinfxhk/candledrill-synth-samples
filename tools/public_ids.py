"""Canonical public sample ids.

Loaders may still pass a retired id. Call ``canonical_public_id`` before
writing metadata, filenames, or any other user-facing catalog. Do not copy
the retired keys into README, NOTICE, or generated JSON.
"""

from __future__ import annotations

import re

# Exact retired id → public id. Longer pack ids are their own keys so they
# are not rewritten as the short index ids.
LEGACY_TO_PUBLIC: dict[str, str] = {
    "SYNTH-NQ-GLOBEX": "SYNTH-TECHIDX-24H",
    "PRACTICE-NQ-GLOBEX": "PRACTICE-TECHIDX-24H",
    "SYNTH-NQ": "SYNTH-IDX1",
    "SYNTH-ES": "SYNTH-IDX2",
    "SYNTH-GC": "SYNTH-METAL1",
}

# Unchanged public id. Kept here so loaders can see it is intentional.
UNCHANGED_PUBLIC_IDS: tuple[str, ...] = ("SYNTH-BTC",)

PUBLIC_PACK_SYMBOL = "SYNTH-TECHIDX-24H"
PUBLIC_IMPORT_SYMBOL = "PRACTICE-TECHIDX-24H"

DISCLAIMER = (
    "SYNTHETIC PRACTICE DATA ONLY. Not index-licensed. "
    "Not exchange, CME, or Nasdaq data. "
    "Not affiliated with CME, Nasdaq, any exchange, any index provider, or any data vendor. "
    "Not a vendor replay. "
    "Hand-tuned near-24h session-like calendar and volume shape for CandleDrill practice. "
    "Independent seed and price level. Educational use only."
)

_RETIRED_SURFACE = re.compile(r"globex|\bNQ\b|\bES\b|\bGC\b", re.IGNORECASE)


def canonical_public_id(symbol: str) -> str:
    """Return the public id. Retired ids are rewritten; current ids pass through.

    Raises ValueError when the result would still show retired branding.
    """
    public = LEGACY_TO_PUBLIC.get(symbol, symbol)
    if _RETIRED_SURFACE.search(public):
        raise ValueError("refusing a user-facing sample id that still uses retired branding")
    return public


def public_disclaimer(text: str | None) -> str:
    """Use the canonical disclaimer when text is missing or still carries retired branding."""
    if text and not _RETIRED_SURFACE.search(text):
        return text
    return DISCLAIMER
