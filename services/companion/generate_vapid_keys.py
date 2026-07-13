#!/usr/bin/env python3
"""Generate VAPID key pair for Web Push. Run once, add output to .env on the VPS.

Requires: pip install py-vapid cryptography (dev-only, not needed in production).
"""

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

key = ec.generate_private_key(ec.SECP256R1())

with open("vapid_private.pem", "wb") as f:
    f.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))

pub_b64 = (
    base64.urlsafe_b64encode(
        key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    .rstrip(b"=")
    .decode()
)

print(f"VAPID_PUBLIC_KEY={pub_b64}")
print("Private key written to vapid_private.pem")
print("Set VAPID_PRIVATE_KEY=/app/data/vapid_private.pem in .env")
