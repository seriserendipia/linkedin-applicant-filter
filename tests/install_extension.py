"""Programmatically register the unpacked extension into the persistent Chrome
profile (~/.chrome-profile) by injecting an entry into Default/Preferences.

Why this exists: Chrome 137+ silently ignores --load-extension. The
normal install path is via the chrome://extensions Load Unpacked UI, but we
want the test pipeline to be hands-free.

Algorithm:
  - Compute the deterministic extension ID for the unpacked dir.
  - Read Preferences.
  - Copy an existing unpacked-extension entry as a template (location:4 = COMMAND_LINE).
  - Swap path + manifest + first_install_time.
  - Write back.

Chrome will pick up the entry next time it launches with the profile.

Idempotent: if our entry is already there, only the manifest is refreshed.
"""
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

PROFILE = Path("/home/agent/.chrome-profile")
PREFS_PATH = PROFILE / "Default" / "Preferences"
EXT_DIR = Path("/workspace/job-applicant-count-filter").resolve()
MANIFEST_PATH = EXT_DIR / "manifest.json"


def ext_id_from_path(path: str) -> str:
    h = hashlib.sha256(path.encode("utf-8")).digest()
    hexed = h[:16].hex()
    return "".join(chr(ord("a") + int(c, 16)) for c in hexed)


def main():
    if not MANIFEST_PATH.is_file():
        sys.exit(f"manifest not found at {MANIFEST_PATH}")
    if not PREFS_PATH.is_file():
        sys.exit(f"Chrome Preferences not found at {PREFS_PATH}")

    ext_id = ext_id_from_path(str(EXT_DIR))
    print(f"extension id (derived from path): {ext_id}")

    manifest = json.loads(MANIFEST_PATH.read_text())
    prefs = json.loads(PREFS_PATH.read_text())

    # Bump the patch version on every install so Chrome treats the on-disk
    # JS files as updated and re-reads them. Without this, Chrome 137+
    # caches the originally-loaded version and silently ignores edits.
    parts = (manifest.get("version") or "0.0.0").split(".")
    while len(parts) < 3: parts.append("0")
    parts[-1] = str(int(parts[-1]) + 1)
    manifest["version"] = ".".join(parts)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")

    settings = prefs.setdefault("extensions", {}).setdefault("settings", {})

    # Find a template: any existing entry with location == 4 (unpacked)
    template = None
    for eid, info in settings.items():
        if info.get("location") == 4 and eid != ext_id:
            template = info
            template_id = eid
            break

    now_us = str(int(time.time() * 1_000_000))  # Chrome stores timestamps in micros

    if ext_id in settings:
        print(f"  → entry already exists, refreshing manifest")
        settings[ext_id]["manifest"] = manifest
        settings[ext_id]["path"] = str(EXT_DIR)
        settings[ext_id]["last_update_time"] = now_us
    elif template is not None:
        print(f"  → copying entry from {template_id} as template")
        entry = json.loads(json.dumps(template))  # deep copy
        entry["manifest"] = manifest
        entry["path"] = str(EXT_DIR)
        entry["first_install_time"] = now_us
        entry["last_update_time"] = now_us
        # Drop has_started_service_worker so Chrome starts SW fresh
        entry.pop("has_started_service_worker", None)
        # Reset disable_reasons so it's enabled
        entry["disable_reasons"] = []
        # Use the granted permissions from our manifest
        perms = list(manifest.get("permissions", []))
        host_perms = list(manifest.get("host_permissions", []))
        entry.setdefault("granted_permissions", {})
        entry["granted_permissions"]["api"] = perms
        entry["granted_permissions"]["explicit_host"] = host_perms
        entry.setdefault("active_permissions", {})
        entry["active_permissions"]["api"] = perms
        entry["active_permissions"]["explicit_host"] = host_perms
        settings[ext_id] = entry
    else:
        sys.exit(
            "no existing unpacked extension to use as template. install one "
            "manually via chrome://extensions → Load unpacked, then rerun."
        )

    # Make sure developer mode is on (required for unpacked extensions to be enabled)
    prefs.setdefault("extensions", {}).setdefault("ui", {})["developer_mode"] = True

    # Backup then write
    bak = PREFS_PATH.with_suffix(".jacf_backup")
    if not bak.exists():
        shutil.copy(PREFS_PATH, bak)
        print(f"  → backed up Preferences to {bak}")
    PREFS_PATH.write_text(json.dumps(prefs, separators=(",", ":")))
    print(f"  → wrote Preferences ({PREFS_PATH.stat().st_size} bytes)")

    # Clear Chrome's SW bytecode cache. This is the only reliable way to make
    # Chrome re-read background.js after we edit it on disk — version bumps
    # alone don't invalidate the cache, only delete-and-respawn does.
    sw_cache = PROFILE / "Default" / "Service Worker"
    for sub in ("ScriptCache", "Database"):
        d = sw_cache / sub
        if d.exists():
            for child in d.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try: child.unlink()
                    except FileNotFoundError: pass
    print(f"  → cleared SW ScriptCache + Database")
    print(f"\nNext launch of Chrome on this profile will load the latest extension code.")
    print(f"To verify:  chrome://extensions/  →  look for '{manifest['name']}'")


if __name__ == "__main__":
    main()
