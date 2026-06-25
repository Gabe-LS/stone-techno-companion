#!/usr/bin/env python3
"""Generate VAPID key pair for Web Push. Run once, add output to .env on the VPS."""

from py_vapid import Vapid

v = Vapid()
v.generate_keys()
print(f"VAPID_PRIVATE_KEY={v.private_pem().decode().strip()}")
print(f"VAPID_PUBLIC_KEY={v.public_key}")
