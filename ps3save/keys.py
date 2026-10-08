"""
PARAM.PFD cryptographic constants.

These are the well-known PS3 "pfdtool" keys originally published by flatz
(2012) and reused unchanged by every open-source PS3 save tool since,
including Sony's own leaked savedata keyset as redistributed in
bucanero/apollo-ps3 and bucanero/pfd_sfo_tools (both GPL). They are not a
secret this project is extracting: they ship baked into every PS3 homebrew
save manager (Apollo Save Tool, multiMAN, PS3 Save Resigner, etc.).

Sizes below follow the field names used in pfdtool's pfd_internal.h.
"""

from __future__ import annotations

# Fixed global key used for every save (not account/console specific).
AUTHENTICATION_ID = bytes.fromhex("1010000001000003")  # 8 bytes

# AES-128 key used to wrap/unwrap the 64-byte PARAM.PFD signature block.
SYSCON_MANAGER_KEY = bytes.fromhex("D413B89663E1FE9F75143D3BB4565274")  # 16 bytes

# Used only when a specific game isn't present in a disc-hash-key database.
FALLBACK_DISC_HASH_KEY = bytes.fromhex("D1C1E10B9C547E689B805DCD9710CE8D")  # 16 bytes

# HMAC key used to derive the "real" hash key for PFD version 4 saves.
KEYGEN_KEY = bytes.fromhex("6B1ACEA246B745FD8F93763B920594CD53483B82")  # 20 bytes

SAVEGAME_PARAM_SFO_KEY = bytes.fromhex("0C08000E090504040D010F000406020209060D03")  # 20 bytes
TROPHY_PARAM_SFO_KEY = bytes.fromhex("5D5B647917024E9BB8D330486B996E795D7F4392")   # 20 bytes
TROPSYS_DAT_KEY = bytes.fromhex("B080C40FF358643689281736A6BF15892CFEA436")       # 20 bytes
TROPUSR_DAT_KEY = bytes.fromhex("8711EFF406913F0937F115FAB23DE1A9897A789A")       # 20 bytes
TROPTRNS_DAT_KEY = bytes.fromhex("91EE81555ACC1C4FB5AAE5462CFE1C62A4AF36A5")      # 20 bytes
TROPCONF_SFM_KEY = bytes.fromhex("E2ED33C71C444EEBC1E23D635AD8E82F4ECA4E94")      # 20 bytes
