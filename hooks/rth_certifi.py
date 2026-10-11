# PyInstaller runtime hook: point OpenSSL at certifi's bundled CA file.
#
# python.org's macOS Python makes OpenSSL look for cert.pem inside its own
# framework folder (only filled in by "Install Certificates.command"), which
# doesn't exist on an end user's Mac -- so every HTTPS request (cheats,
# games.conf) would fail with CERTIFICATE_VERIFY_FAILED once frozen.
# Setting SSL_CERT_FILE before any ssl context is created fixes it without
# touching the ps3save package. Harmless on Windows (same CA bundle).
import os

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
