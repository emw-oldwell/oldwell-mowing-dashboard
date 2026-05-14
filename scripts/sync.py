#!/usr/bin/env python3
"""
Sync mowing schedule + photos from SharePoint to this repo.

Reads SharePoint Schedule list via Microsoft Graph (app-only auth),
downloads any new photo attachments, writes data.json.

Env vars expected:
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
  SHAREPOINT_HOST       e.g. oldwellco.sharepoint.com
  SHAREPOINT_SITE_PATH  e.g. /sites/Mowing
  GITHUB_REPO           e.g. emw-oldwell/oldwell-mowing-dashboard  (for photo URLs)
"""
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

REPO_ROOT = Path(__file__).resolve().parent.parent
PHOTOS_DIR = REPO_ROOT / "photos"
PHOTOS_DIR.mkdir(exist_ok=True)
DATA_FILE = REPO_ROOT / "data.json"


def get_token() -> str:
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
    """GET a Microsoft Graph path. `path` starts with /, no host."""
    r = requests.get(
        f"https://graph.microsoft.com/v1.0{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def graph_all(token: str, path: str, params: dict | None = None) -> list[dict]:
    """Page through a Graph collection, returning all value items."""
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


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


def main() -> int:
    print("Fetching token…", flush=True)
    token = get_token()

    print(f"Resolving site {SP_HOST}{SP_SITE_PATH}…", flush=True)
    site = graph(token, f"/sites/{SP_HOST}:{SP_SITE_PATH}")
    site_id = site["id"]
    print(f"  site id: {site_id}", flush=True)

    print("Finding Schedule list…", flush=True)
    lists = graph_all(token, f"/sites/{site_id}/lists")
    schedule = next((l for l in lists if l["displayName"] == "Schedule"), None)
    if not schedule:
        print("ERROR: Schedule list not found", file=sys.stderr)
        return 1
    list_id = schedule["id"]
    print(f"  list id: {list_id}", flush=True)

    print("Fetching Schedule items with attachments…", flush=True)
    # Expand attachments and fields together
    items = graph_all(
        token,
        f"/sites/{site_id}/lists/{list_id}/items",
        params={"$expand": "fields,attachments", "$top": "100"},
    )
    print(f"  got {len(items)} items", flush=True)

    # Reshape into our schema, sync photo files
    out_jobs: list[dict[str, Any]] = []
    new_photos_count = 0
    for it in items:
        f = it.get("fields", {})
        atts_in = it.get("attachments", []) or []
        atts_out: list[dict] = []
        for att in atts_in:
            fname = att.get("name") or att.get("displayName") or "attachment"
            local_name = f"{f.get('JobID', it['id'])}_{safe_name(fname)}"
            local_path = PHOTOS_DIR / local_name
            url = att.get("@microsoft.graph.downloadUrl") or att.get("contentUrl")
            if not local_path.exists() and url:
                try:
                    bin_r = requests.get(url, timeout=60)
                    bin_r.raise_for_status()
                    local_path.write_bytes(bin_r.content)
                    new_photos_count += 1
                    print(f"  + photo: {local_name} ({len(bin_r.content)} bytes)", flush=True)
                except Exception as e:
                    print(f"  ! photo failed: {local_name}: {e}", file=sys.stderr, flush=True)
            atts_out.append(
                {
                    "FileName": fname,
                    "ServerRelativeUrl": f"https://raw.githubusercontent.com/{GH_REPO}/main/photos/{local_name}",
                }
            )
        out_jobs.append(
            {
                "ID": int(it["id"]),
                "JobID": f.get("JobID"),
                "PropertyID": f.get("PropertyID"),
                "PropertyNickname": f.get("PropertyNickname"),
                "ScheduledDate": f.get("ScheduledDate"),
                "SessionNumber": f.get("SessionNumber"),
                "Status": f.get("Status"),
                "TypeID": f.get("TypeID"),
                "ContractorName": f.get("ContractorName"),
                "Attachments": atts_out,
            }
        )

    data = {
        "lastUpdated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "jobs": out_jobs,
    }
    DATA_FILE.write_text(json.dumps(data, separators=(",", ":")))
    print(f"Wrote {DATA_FILE} with {len(out_jobs)} jobs, {new_photos_count} new photos", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
