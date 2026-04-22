"""Apply community patches to the installed twikit package.

Runs after `uv pip install -r requirements.txt` during install/update.
Overwrites three files inside the venv's twikit install with patched
versions vendored in ./patches/twikit/, covering two known bugs in
twikit 2.3.3 caused by recent changes on x.com:

  1. "Couldn't get KEY_BYTE indices"      — PR #416 (transaction.py)
  2. KeyError: 'urls' / missing fields    — PR #418 (user.py, client.py)

Safe to run repeatedly; each run is an idempotent copy.

If the upstream twikit maintainer publishes an official fix that supersedes
these, delete the matching file from patches/twikit/ and this script will
silently skip it.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


# Patches were authored against this exact upstream version. If twikit ships
# a newer release, skip patching to avoid clobbering fixes the maintainer may
# have already applied. Bump this (or delete stale files in patches/twikit/)
# when curating a new target version.
TARGET_TWIKIT_VERSION = "2.3.3"

PATCH_MAP = [
    # (source in ./patches,        destination inside twikit package)
    ("patches/twikit/user.py",                              "user.py"),
    ("patches/twikit/client.py",                            "client/client.py"),
    ("patches/twikit/x_client_transaction/transaction.py",  "x_client_transaction/transaction.py"),
]


def find_twikit() -> tuple[Path | None, str | None]:
    """Locate the installed twikit package and return (root_path, version)."""
    try:
        import twikit
    except ImportError:
        return None, None
    root = Path(sys.modules["twikit"].__file__).parent
    version = getattr(twikit, "__version__", None)
    return root, version


def main() -> int:
    root = Path(__file__).resolve().parent
    twikit_root, twikit_version = find_twikit()
    if twikit_root is None:
        print("[patches] twikit not installed — skipping.")
        return 0

    if twikit_version != TARGET_TWIKIT_VERSION:
        print(
            f"[patches] twikit version is {twikit_version!r}, "
            f"expected {TARGET_TWIKIT_VERSION!r} — skipping to avoid clobbering "
            "upstream fixes. Update patches/twikit/ and TARGET_TWIKIT_VERSION "
            "in apply_twikit_patches.py if these patches are still needed."
        )
        return 0

    applied, skipped = 0, 0
    for rel_src, rel_dst in PATCH_MAP:
        src = root / rel_src
        dst = twikit_root / rel_dst
        if not src.exists():
            print(f"[patches] skip (missing source): {rel_src}")
            skipped += 1
            continue
        if not dst.parent.exists():
            print(f"[patches] skip (target dir missing): {dst.parent}")
            skipped += 1
            continue
        shutil.copyfile(src, dst)
        print(f"[patches] applied: {rel_src} -> {dst}")
        applied += 1

    print(f"[patches] done. applied={applied} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
