#!/usr/bin/env python3
"""
Sync mowing schedule + photos from SharePoint to this repo.

Reads the Schedule list via SharePoint REST API (app-only auth),
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
SP_HOST = os.environ["SHAREPOINT_HOST"]                # e.g. oldwellco.sharepoint.com
SP_SITE_PATH = os.environ["SHAREPOINT_SITE_PATH"]      # e.g. /sites/Mowing
GH_REPO = os.environ.get("GITHUB_REPO", "emw-oldwell/oldwell-mowing-dashboard")

SP_RESOURCE = f"https://{SP_HOST}"
SP_BASE = f"{SP_RESOURCE}{SP_SITE_PATH}"

REPO_ROOT = Path(__file__).resolve().parent.parent
PHOTOS_DIR = REPO_ROOT / "photos"
PHOTOS_DIR.mkdir(exist_ok=True)
DATA_FILE = REPO_ROOT / "data.json"


def get_sp_token() -> str:
    r = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": f"{SP_RESOURCE}/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def sp_get(token: str, path: str) -> dict:
    """GET a SharePoint REST API endpoint relative to the site (path starts with /_api/...)."""
    r = requests.get(
        f"{SP_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json;odata=nometadata"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def sp_get_all(token: str, path: str) -> list[dict]:
    """Page through an SP REST collection. Returns flat list of 'value' items."""
    items: list[dict] = []
    url = f"{SP_BASE}{path}"
    while url:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json;odata=nometadata"},
            timeout=30,
        )
        r.raise_for_status()
        j = r.json()
        items.extend(j.get("value", []))
        url = j.get("odata.nextLink") or j.get("@odata.nextLink")
    return items


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


def main() -> int:
    print(f"Fetching SP token for {SP_RESOURCE}…", flush=True)
    token = get_sp_token()

    print("Fetching Schedule items with attachments…", flush=True)
    items = sp_get_all(
        token,
        "/_api/web/lists/getbytitle('Schedule')/items?$top=200&$expand=AttachmentFiles",
    )
    print(f"  got {len(items)} items", flush=True)

    out_jobs: list[dict[str, Any]] = []
    new_photos_count = 0
    for it in items:
        atts_in = it.get("AttachmentFiles", []) or []
        atts_out: list[dict] = []
        for att in atts_in:
            fname = att.get("FileName") or "attachment"
            srv_rel = att.get("ServerRelativeUrl") or ""
            local_name = f"{it.get('JobID', it.get('Id', it.get('ID', 'x')))}_{safe_name(fname)}"
            local_path = PHOTOS_DIR / local_name
            if not local_path.exists() and srv_rel:
                try:
                    bin_r = requests.get(
                        f"{SP_RESOURCE}{srv_rel}",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=60,
                    )
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
                "ID": it.get("Id") or it.get("ID"),
                "JobID": it.get("JobID"),
                "PropertyID": it.get("PropertyID"),
                "PropertyNickname": it.get("PropertyNickname"),
                "ScheduledDate": it.get("ScheduledDate"),
                "SessionNumber": it.get("SessionNumber"),
                "Status": it.get("Status"),
                "TypeID": it.get("TypeID"),
                "ContractorName": it.get("ContractorName"),
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
