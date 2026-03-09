#!/usr/bin/env python3
"""
WebDAV Sync Script for Jianguoyun (坚果云)

Syncs notes from a WebDAV server (e.g. Jianguoyun / 坚果云) to the local
repository.  Designed to run in GitHub Actions on a schedule.

Features:
  - Connects to any WebDAV server using Basic Auth
  - Filters files using whitelist and exclude glob patterns
  - Detects remote changes via ETag / Last-Modified tracking
  - Only downloads changed or new files
  - Removes locally-synced files that were deleted on the remote

Credentials are read from environment variables:
  WEBDAV_USER      – WebDAV username (e.g. your Jianguoyun email)
  WEBDAV_PASSWORD  – WebDAV app-specific password

Usage:
  python scripts/webdav_sync.py --config webdav_config.yml [--out .]
"""

import os
import sys
import json
import fnmatch
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests
import yaml

# ── Constants ─────────────────────────────────────────────────────────────────

DAV_NS = "DAV:"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dav(tag: str) -> str:
    """Return a fully-qualified DAV: namespace tag for ElementTree."""
    return f"{{{DAV_NS}}}{tag}"


def load_config(config_path: str) -> dict:
    """Load YAML configuration file."""
    with open(config_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_manifest(manifest_path: str) -> dict:
    """Load the sync manifest that tracks remote file state."""
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_manifest(manifest_path: str, manifest: dict) -> None:
    """Persist the sync manifest to disk."""
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)


# ── WebDAV operations ─────────────────────────────────────────────────────────

_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:">'
    "<d:prop>"
    "<d:getlastmodified/>"
    "<d:getcontentlength/>"
    "<d:resourcetype/>"
    "<d:getetag/>"
    "</d:prop>"
    "</d:propfind>"
)


def propfind(session: requests.Session, url: str, depth: str = "1") -> list[dict]:
    """
    Send a PROPFIND request and return a list of parsed entries.

    Each entry is a dict with keys:
      href, is_dir, etag, last_modified, content_length
    """
    headers = {
        "Depth": depth,
        "Content-Type": "application/xml; charset=utf-8",
    }
    resp = session.request(
        "PROPFIND", url, headers=headers, data=_PROPFIND_BODY.encode("utf-8"),
    )
    resp.raise_for_status()

    entries: list[dict] = []
    root = ET.fromstring(resp.content)

    for response_el in root.findall(_dav("response")):
        href_el = response_el.find(_dav("href"))
        if href_el is None:
            continue

        propstat = response_el.find(_dav("propstat"))
        if propstat is None:
            continue
        prop = propstat.find(_dav("prop"))
        if prop is None:
            continue

        resource_type = prop.find(_dav("resourcetype"))
        is_dir = (
            resource_type is not None
            and resource_type.find(_dav("collection")) is not None
        )

        etag_el = prop.find(_dav("getetag"))
        lm_el = prop.find(_dav("getlastmodified"))
        cl_el = prop.find(_dav("getcontentlength"))

        entries.append({
            "href": unquote(href_el.text or ""),
            "is_dir": is_dir,
            "etag": (etag_el.text or "").strip('"') if etag_el is not None else "",
            "last_modified": lm_el.text or "" if lm_el is not None else "",
            "content_length": (
                int(cl_el.text) if cl_el is not None and cl_el.text else 0
            ),
        })

    return entries


def list_remote_files(
    session: requests.Session, base_url: str, remote_base_href: str,
) -> dict[str, dict]:
    """
    Recursively list every file under *base_url* via PROPFIND (Depth: 1).

    Returns ``{relative_path: {etag, last_modified, content_length}}``.
    """
    files: dict[str, dict] = {}
    dirs_to_visit: list[str] = [""]

    while dirs_to_visit:
        rel_dir = dirs_to_visit.pop(0)
        url = (
            base_url.rstrip("/") + "/" + quote(rel_dir, safe="/")
            if rel_dir
            else base_url
        )
        if not url.endswith("/"):
            url += "/"

        try:
            entries = propfind(session, url, depth="1")
        except requests.HTTPError as exc:
            print(f"  Warning: PROPFIND failed for '{rel_dir or '/'}': {exc}")
            continue

        for entry in entries:
            entry_path = entry["href"]

            # Compute relative path by stripping the remote base prefix
            if remote_base_href and entry_path.startswith(remote_base_href):
                rel_path = entry_path[len(remote_base_href):].strip("/")
            else:
                continue

            if not rel_path:
                continue  # skip the directory itself

            if entry["is_dir"]:
                dirs_to_visit.append(rel_path)
            else:
                files[rel_path] = {
                    "etag": entry["etag"],
                    "last_modified": entry["last_modified"],
                    "content_length": entry["content_length"],
                }

    return files


def download_file(
    session: requests.Session, base_url: str, remote_path: str, local_path: str,
) -> None:
    """Download a single file from the WebDAV server."""
    url = base_url.rstrip("/") + "/" + quote(remote_path, safe="/")
    resp = session.get(url)
    resp.raise_for_status()

    dest = Path(local_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)


# ── Filtering ─────────────────────────────────────────────────────────────────

def _matches_any(path: str, patterns: list[str]) -> bool:
    """Return True if *path* matches at least one glob pattern."""
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def filter_files(
    files: dict[str, dict], whitelist: list[str], exclude: list[str],
) -> dict[str, dict]:
    """Keep only files that match *whitelist* and do not match *exclude*."""
    result: dict[str, dict] = {}
    for path, info in files.items():
        if exclude and _matches_any(path, exclude):
            continue
        if whitelist and not _matches_any(path, whitelist):
            continue
        result[path] = info
    return result


# ── Core sync logic ──────────────────────────────────────────────────────────

def sync(config_path: str, output_dir: str, manifest_path: str | None = None) -> bool:
    """
    Synchronise files from a WebDAV server to *output_dir*.

    *manifest_path* is the location of the JSON manifest that tracks the remote
    file state (ETags, last-modified timestamps).  When ``None`` it defaults to
    ``<output_dir>/.webdav_manifest.json``.

    Returns ``True`` if any files were added, updated, or deleted.
    """
    config = load_config(config_path)
    webdav_cfg = config.get("webdav", {})

    base_url = webdav_cfg.get("url", "").rstrip("/")
    remote_path = webdav_cfg.get("remote_path", "").strip("/")
    whitelist = config.get("whitelist", [])
    exclude = config.get("exclude", [])

    # -- credentials ----------------------------------------------------------
    user = os.environ.get("WEBDAV_USER", "")
    password = os.environ.get("WEBDAV_PASSWORD", "")
    missing = [v for v in ("WEBDAV_USER", "WEBDAV_PASSWORD") if not os.environ.get(v)]
    if missing:
        print(f"Error: missing environment variable(s): {', '.join(missing)}")
        sys.exit(1)
    if not base_url:
        print("Error: webdav.url is not configured.")
        sys.exit(1)

    # -- build full URL -------------------------------------------------------
    full_url = (
        f"{base_url}/{quote(remote_path, safe='/')}" if remote_path else base_url
    )
    if not full_url.endswith("/"):
        full_url += "/"

    remote_base_href = urlparse(full_url).path

    # -- manifest -------------------------------------------------------------
    if manifest_path is None:
        manifest_path = os.path.join(output_dir, ".webdav_manifest.json")
    manifest = load_manifest(manifest_path)

    # -- session --------------------------------------------------------------
    session = requests.Session()
    session.auth = (user, password)

    print(f"WebDAV server : {base_url}")
    print(f"Remote path   : /{remote_path}")
    print(f"Output dir    : {output_dir}")

    # -- list & filter --------------------------------------------------------
    print("\nListing remote files …")
    remote_files = list_remote_files(session, full_url, remote_base_href)
    print(f"  Found {len(remote_files)} file(s) on remote")

    filtered = filter_files(remote_files, whitelist, exclude)
    print(f"  {len(filtered)} file(s) match whitelist after exclusions")

    # -- download new / changed files -----------------------------------------
    changed = False
    downloaded = 0
    deleted = 0

    for rel_path, info in filtered.items():
        local_file = os.path.join(output_dir, rel_path)
        prev = manifest.get(rel_path, {})

        needs_update = False
        reason = ""
        if not os.path.exists(local_file):
            needs_update, reason = True, "new"
        elif info["etag"] and prev.get("etag") != info["etag"]:
            needs_update, reason = True, "etag changed"
        elif info["last_modified"] and prev.get("last_modified") != info["last_modified"]:
            needs_update, reason = True, "modified"
        elif not prev:
            needs_update, reason = True, "not in manifest"

        if needs_update:
            print(f"  ↓ [{reason}] {rel_path}")
            try:
                download_file(session, full_url, rel_path, local_file)
                downloaded += 1
                changed = True
            except requests.HTTPError as exc:
                print(f"    ✗ download failed: {exc}")
                continue

        manifest[rel_path] = {
            "etag": info["etag"],
            "last_modified": info["last_modified"],
            "content_length": info["content_length"],
        }

    # -- remove files deleted on remote ---------------------------------------
    stale = [p for p in manifest if p not in filtered]
    for rel_path in stale:
        local_file = os.path.join(output_dir, rel_path)
        if os.path.exists(local_file):
            print(f"  ✗ [deleted remotely] {rel_path}")
            os.remove(local_file)
            deleted += 1
            changed = True
            # clean up empty parent directories
            parent = Path(local_file).parent
            while parent != Path(output_dir).resolve() and parent.exists():
                if not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
                else:
                    break
        del manifest[rel_path]

    # -- persist manifest -----------------------------------------------------
    save_manifest(manifest_path, manifest)

    print(f"\nSync result: {downloaded} downloaded, {deleted} deleted")
    return changed


# ── CLI entry-point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync notes from WebDAV (e.g. 坚果云) to local repository.",
    )
    parser.add_argument(
        "--config",
        default="webdav_config.yml",
        help="Path to the WebDAV config file (default: webdav_config.yml)",
    )
    parser.add_argument(
        "--out",
        default=".",
        help="Output directory for synced files (default: current directory)",
    )
    args = parser.parse_args()

    changed = sync(args.config, args.out)

    # Expose result to GitHub Actions via $GITHUB_OUTPUT
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")

    if changed:
        print("\n✓ Changes detected and synced.")
    else:
        print("\n✓ No changes detected.")


if __name__ == "__main__":
    main()
