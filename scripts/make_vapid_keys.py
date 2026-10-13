"""Make the key pair browser notifications need. Run once, then put the two lines in Render's environment (and .env).

    python scripts/make_vapid_keys.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import notify  # noqa: E402

public, private = notify.generate_keys()
print(f"VAPID_PUBLIC_KEY={public}")
print(f"VAPID_PRIVATE_KEY={private}")
print("VAPID_SUBJECT=mailto:you@example.com   # any email address of yours")
print("\nKeep the private key secret. If you ever make a new pair, everyone must turn notifications on again.", file=sys.stderr)
