#!/usr/bin/env python3
"""
Sync mowing schedule + photos from SharePoint to this repo.

Reads via Microsoft Graph (app-only):
  - Schedule list items (text fields)
  - Photos from the Mowing site's Documents library under /Photos/{JobID}/*
    (the Photo Intake Power Automate flow drops a copy of each contractor
    photo there; SharePoint list attachments aren't reachable app-only
    because Azure ACS auth is retired for new tenants)

New photos found in /Photos/{JobID}/ are downloaded into ./photos/
and referenced from data.json via raw.githubusercontent URLs.

Env vars expected:
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
  SHAREPOINT_HOST       e.g. oldwellco.sharepoint.com
  SHAREPOINT_SITE_PATH  e.g. /sites/Mowing
  GITHUB_REPO           e.g. emw-oldwell/oldwell-mowing-dashboard  (for photo URLs)
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

TENANT_ID = os.environ["AZURE_TENANT_ID"]
CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]
SP_HOST = os.environ["SHAREPOINT_HOST"]
SP_SITE_PATH = os.environ["SHAREPOINT_SITE_PATH"]
GH_REPO = os.environ.get("GITHUB_REPO", "emw-oldwell/oldwell-mowing-dashboard")

PHOTOS_LIBRARY = "Documents"  # display name of the doc library
PHOTOS_ROOT = "Photos"        # folder inside the library where the flow drops photos

REPO_ROOT = Path(__file__).resolve().parent.parent
PHOTOS_DIR = REPO_ROOT / "photos"
DATA_FILE = REPO_ROOT / "data.json"


def get_graph_token() -> str:
    r = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def graph(token: str, path: str, params: dict | None = None) -> dict:
    r = requests.get(
        f"https://graph.microsoft.com/v1.0{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def graph_all(token: str, path: str, params: dict | None = None) -> list[dict]:
    items: list[dict] = []
    url = f"https://graph.microsoft.com/v1.0{path}"
    first = True
    while url:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params if first else None,
            timeout=30,
        )
        r.raise_for_status()
        j = r.json()
        items.extend(j.get("value", []))
        url = j.get("@odata.nextLink")
        first = False
    return items


def safe_filename(name: str) -> str:
    name = name.replace("\\", "_").replace("/", "_")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def find_drive_id(token: str, site_id: str) -> str | None:
    drives = graph_all(token, f"/sites/{site_id}/drives")
    for d in drives:
        if d.get("name") == PHOTOS_LIBRARY:
            return d["id"]
    # fallback: first drive
    return drives[0]["id"] if drives else None


def list_job_folders(token: str, drive_id: str) -> list[dict]:
    """Children of /Photos/ in the library. Returns folders (one per JobID, by name)."""
    try:
        r = requests.get(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{PHOTOS_ROOT}:/children",
            headers={"Authorization": f"Bearer {token}"},
            params={"$top": "500"},
            timeout=30,
        )
        if r.status_code == 404:
            print(f"  /{PHOTOS_ROOT}/ folder doesn't exist yet (no photos synced)", flush=True)
            return []
        r.raise_for_status()
        return r.json().get("value", [])
    except requests.HTTPError as e:
        print(f"  WARN: couldn't list /{PHOTOS_ROOT}/: {e}", file=sys.stderr)
        return []


def list_files_in_folder(token: str, drive_id: str, folder_item_id: str) -> list[dict]:
    return graph_all(token, f"/drives/{drive_id}/items/{folder_item_id}/children", params={"$top": "200"})


def download_photo(token: str, drive_id: str, item_id: str, dest: Path) -> bool:
    r = requests.get(
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content",
        headers={"Authorization": f"Bearer {token}"},
        allow_redirects=True,
        timeout=120,
    )
    if not r.ok:
        print(f"    failed: {r.status_code} {r.text[:200]}", file=sys.stderr)
        return False
    dest.write_bytes(r.content)
    return True


def collect_photos(token: str, drive_id: str) -> dict[str, list[dict]]:
    """
    Walk /Photos/{JobID}/* in the doc library, mirror new files into ./photos/,
    return {JobID: [{FileName, ServerRelativeUrl}]}.
    """
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[dict]] = {}
    job_folders = list_job_folders(token, drive_id)
    for f in job_folders:
        if "folder" not in f:
            continue
        job_id = f.get("name")
        if not job_id:
            continue
        files = list_files_in_folder(token, drive_id, f["id"])
        descriptors: list[dict] = []
        for fi in files:
            if "file" not in fi:
                continue
            raw_name = fi.get("name") or ""
            if not raw_name:
                continue
            local_name = f"{job_id}_{safe_filename(raw_name)}"
            local_path = PHOTOS_DIR / local_name
            if not local_path.exists():
                print(f"  downloading {local_name}…", flush=True)
                if not download_photo(token, drive_id, fi["id"], local_path):
                    continue
            descriptors.append(
                {
                    "FileName": raw_name,
                    "ServerRelativeUrl": f"https://raw.githubusercontent.com/{GH_REPO}/main/photos/{local_name}",
                }
            )
        if descriptors:
            out[job_id] = descriptors
    return out


def discover_existing_photos_for_job(job_id: str) -> list[dict]:
    """Backstop: any pre-existing {JobID}_* file in ./photos/ (e.g. ones pushed manually before this flow existed)."""
    if not job_id or not PHOTOS_DIR.exists():
        return []
    out = []
    for p in sorted(PHOTOS_DIR.glob(f"{job_id}_*")):
        out.append(
            {
                "FileName": p.name.split("_", 1)[-1] if "_" in p.name else p.name,
                "ServerRelativeUrl": f"https://raw.githubusercontent.com/{GH_REPO}/main/photos/{p.name}",
            }
        )
    return out


def main() -> int:
    print("Fetching Graph token…", flush=True)
    token = get_graph_token()

    print(f"Resolving site {SP_HOST}{SP_SITE_PATH}…", flush=True)
    site = graph(token, f"/sites/{SP_HOST}:{SP_SITE_PATH}")
    site_id = site["id"]

    print("Finding Schedule list…", flush=True)
    lists = graph_all(token, f"/sites/{site_id}/lists")
    schedule = next((l for l in lists if l["displayName"] == "Schedule"), None)
    if not schedule:
        print("ERROR: Schedule list not found", file=sys.stderr)
        return 1
    list_id = schedule["id"]

    print("Fetching Schedule items (Graph)…", flush=True)
    items = graph_all(
        token,
        f"/sites/{site_id}/lists/{list_id}/items",
        params={"$expand": "fields", "$top": "100"},
    )
    print(f"  got {len(items)} items", flush=True)

    print(f"Finding {PHOTOS_LIBRARY!r} drive…", flush=True)
    drive_id = find_drive_id(token, site_id)
    photo_map: dict[str, list[dict]] = {}
    if drive_id:
        print(f"  drive id {drive_id}", flush=True)
        photo_map = collect_photos(token, drive_id)
        print(f"  jobs with photos in /{PHOTOS_ROOT}/: {len(photo_map)}", flush=True)
    else:
        print("  WARN: no drive found on site", file=sys.stderr)

    out_jobs: list[dict[str, Any]] = []
    for it in items:
        f = it.get("fields", {})
        job_id = f.get("JobID")
        atts = photo_map.get(job_id) if job_id else None
        if not atts:
            atts = discover_existing_photos_for_job(job_id) if job_id else []
        out_jobs.append(
            {
                "ID": int(it["id"]),
                "JobID": job_id,
                "PropertyID": f.get("PropertyID"),
                "PropertyNickname": f.get("PropertyNickname"),
                "ScheduledDate": f.get("ScheduledDate"),
                "SessionNumber": f.get("SessionNumber"),
                "Status": f.get("Status"),
                "TypeID": f.get("TypeID"),
                "ContractorName": f.get("ContractorName"),
                "Attachments": atts,
            }
        )

    data = {
        "lastUpdated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "jobs": out_jobs,
    }
    DATA_FILE.write_text(json.dumps(data, separators=(",", ":")))
    print(f"Wrote {DATA_FILE} with {len(out_jobs)} jobs", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
