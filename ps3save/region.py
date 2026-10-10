"""Best-effort PS3 product-code -> region/distribution guess, derived
from Sony's own content-ID conventions. This is community-reverse-
engineered knowledge (never an official Sony list) but very stable
across the PS3 library -- and unrelated to games.conf, which doesn't
carry region info at all.

Prefix shape is 4 letters + 5 digits (see cheats.PRODUCT_CODE_RE):
  1st letter : B = Blu-ray disc (retail), N = PSN (digital)
  3rd letter : E = Europe, U = North America, J = Japan, A/H = Asia,
               K = Korea

Codes that don't start with B/N, or whose 3rd letter isn't one of the
above, are left unlabeled rather than guessed -- test/dev saves and a
handful of obscure prefixes (demos, betas, internal tools) don't follow
this scheme at all.
"""

from __future__ import annotations

_REGION_BY_LETTER = {
    "E": "EU",
    "U": "NA",
    "J": "JP",
    "A": "Asia",
    "H": "Asia",
    "K": "Korea",
}


def region_label(product_code: str) -> str | None:
    """"(EU)", "(NA, Digital)", etc., or None if the prefix doesn't match
    the known disc/PSN + region-letter scheme."""
    if len(product_code) < 4:
        return None
    distribution = product_code[0].upper()
    if distribution not in ("B", "N"):
        return None
    region = _REGION_BY_LETTER.get(product_code[2].upper())
    if region is None:
        return None
    parts = [region]
    if distribution == "N":
        parts.append("Digital")
    return f"({', '.join(parts)})"
