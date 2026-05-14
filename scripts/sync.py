#!/usr/bin/env python3
"""
Sync mowing schedule from SharePoint to this repo.

Reads the Schedule list via Microsoft Graph (app-only auth), writes data.json.
Photos are NOT auto-synced here — they're managed in a separate workflow
(see PHOTOS.md). Existing photo URLs in data.json are preserved across runs.

Env vars expected:
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
  SHAREPOINT_HOST       e.g. oldwellco.sharepoint.com
  SHAREPOINT_SITE_PATH  e.g. /sites/Mowing
  GITHUB_REPO           e.g. emw-oldwell/oldwell-mowing-dashboard  (for photo URLs)
"""
import json
import os
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


def load_existing_attachments() -> dict[str, list[dict]]:
    """Map JobID -> existing Attachments[] from previous data.json. Preserves photo URLs across syncs."""
    if not DATA_FILE.exists():
        return {}
    try:
        prev = json.loads(DATA_FILE.read_text())
    except Exception:
        return {}
    return {j.get("JobID"): j.get("Attachments", []) for j in prev.get("jobs", []) if j.get("JobID")}


def discover_photos_for_job(job_id: str) -> list[dict]:
    """Look in the local photos/ directory for any file matching {JobID}_*.* pattern."""
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
    print("Fetching token…", flush=True)
    token = get_token()

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

    print("Fetching Schedule items…", flush=True)
    items = graph_all(
        token,
        f"/sites/{site_id}/lists/{list_id}/items",
        params={"$expand": "fields", "$top": "100"},
    )
    print(f"  got {len(items)} items", flush=True)

    existing_atts = load_existing_attachments()

    out_jobs: list[dict[str, Any]] = []
    for it in items:
        f = it.get("fields", {})
        job_id = f.get("JobID")
        # Prefer discovered photos (from local /photos/ folder), fall back to previous data.json
        atts = discover_photos_for_job(job_id) if job_id else []
        if not atts:
            atts = existing_atts.get(job_id, [])
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
