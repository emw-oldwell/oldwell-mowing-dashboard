#!/usr/bin/env python3
"""
Sync mowing schedule + photos between SharePoint and this repo.

READ DIRECTION (always on, requires Sites.Read.All Application permission):
  - Schedule list items (text fields)
  - Photos from the Mowing site's Documents library under /Photos/{JobID}/*

WRITE DIRECTION (requires Sites.ReadWrite.All — admin consent in Azure):
  - Pushes events.json overlays back to SharePoint Schedule rows:
      * status=Done / finishedAt → SharePoint Status = "Done"
      * rescheduledTo            → SharePoint ScheduledDate
      * reassignedTo             → SharePoint ContractorName
      * typeOverride             → SharePoint TypeID
  - Creates new SharePoint rows for app-added customJobs (Add Property flow)
  - Tombstoned (deleted) and cancelled overlays are NOT pushed in v1 —
    accepted divergence. SharePoint keeps the original row; the app hides it.
  - Degrades gracefully: if Sites.ReadWrite.All isn't granted yet, the push
    step logs a hint and the read direction continues to work normally.

CONFLICT MODEL: app overlays always win. If someone edits a row in
SharePoint directly AND that field has an existing app overlay, the next
sync overwrites the SharePoint edit. Tell office to edit in the app, not
SP, for schedule changes. SP edits to NEW jobs (no overlay yet) are safe.

Env vars expected:
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
  SHAREPOINT_HOST       e.g. oldwellco.sharepoint.com
  SHAREPOINT_SITE_PATH  e.g. /sites/Mowing
  GITHUB_REPO           e.g. emw-oldwell/oldwell-mowing-dashboard
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
EVENTS_FILE = REPO_ROOT / "events.json"


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


def normalize_iso(s: str | None) -> str | None:
    """Normalize an ISO timestamp for equality comparison between app overlays
    and SharePoint values. The app emits e.g. "2026-05-01T16:00:00.000Z" but
    SharePoint stores it as "2026-05-01T16:00:00Z" (no millis). Without this,
    push compare misfires every sync — re-pushes the same value, no-op writes
    forever."""
    if not s:
        return s
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        return s


def read_events_json() -> dict[str, Any]:
    if not EVENTS_FILE.exists():
        return {"events": {}, "customJobs": [], "tombstones": {}, "crews": [], "properties": []}
    try:
        j = json.loads(EVENTS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"events": {}, "customJobs": [], "tombstones": {}, "crews": [], "properties": []}
    j.setdefault("events", {})
    j.setdefault("customJobs", [])
    j.setdefault("tombstones", {})
    return j


def push_overlays_to_sharepoint(
    token: str,
    site_id: str,
    list_id: str,
    sp_items_by_jobid: dict[str, dict],
    events_data: dict,
) -> dict[str, int | bool]:
    """Apply app overlays + customJobs back to SharePoint Schedule.
    Returns {updated, created, failed, skipped, permission_denied}.
    Tolerates 403 cleanly (Sites.ReadWrite.All not granted yet)."""
    events = events_data.get("events") or {}
    custom_jobs = events_data.get("customJobs") or []
    tombstones = events_data.get("tombstones") or {}

    updated = 0
    created = 0
    failed = 0
    skipped = 0

    def patch_fields(item_id: str, fields: dict) -> tuple[bool, bool]:
        """Returns (ok, permission_denied)."""
        try:
            r = requests.patch(
                f"https://graph.microsoft.com/v1.0/sites/{site_id}/lists/{list_id}/items/{item_id}/fields",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=fields,
                timeout=30,
            )
        except requests.RequestException as e:
            print(f"    ERROR patch: {e}", file=sys.stderr)
            return False, False
        if r.status_code in (401, 403):
            return False, True
        if not r.ok:
            print(f"    FAILED patch {item_id}: {r.status_code} {r.text[:200]}", file=sys.stderr)
            return False, False
        return True, False

    def post_item(fields: dict) -> tuple[bool, bool]:
        try:
            r = requests.post(
                f"https://graph.microsoft.com/v1.0/sites/{site_id}/lists/{list_id}/items",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"fields": fields},
                timeout=30,
            )
        except requests.RequestException as e:
            print(f"    ERROR post: {e}", file=sys.stderr)
            return False, False
        if r.status_code in (401, 403):
            return False, True
        if not r.ok:
            print(f"    FAILED post {fields.get('JobID')}: {r.status_code} {r.text[:200]}", file=sys.stderr)
            return False, False
        return True, False

    # 1) Push overlays onto existing SharePoint rows
    for job_id, overlay in events.items():
        if job_id in tombstones:
            skipped += 1
            continue
        sp_item = sp_items_by_jobid.get(job_id)
        if not sp_item:
            # No matching SP row — either a customJob handled below, or a stale overlay
            continue
        sp_fields = sp_item.get("fields", {})
        item_id = sp_item.get("id")

        to_update: dict[str, Any] = {}
        # Status: only push Done (intermediate/cancelled stay app-only in v1)
        if (overlay.get("status") == "Done" or overlay.get("finishedAt")) and sp_fields.get("Status") != "Done":
            to_update["Status"] = "Done"
        # ScheduledDate — compare normalized (SP strips millis on storage)
        new_date = overlay.get("rescheduledTo")
        if new_date and normalize_iso(sp_fields.get("ScheduledDate")) != normalize_iso(new_date):
            to_update["ScheduledDate"] = new_date
        # ContractorName
        new_contractor = overlay.get("reassignedTo")
        if new_contractor and sp_fields.get("ContractorName") != new_contractor:
            to_update["ContractorName"] = new_contractor
        # TypeID
        new_type = overlay.get("typeOverride")
        if new_type and sp_fields.get("TypeID") != new_type:
            to_update["TypeID"] = new_type

        if not to_update:
            continue
        ok, denied = patch_fields(item_id, to_update)
        if denied:
            print(
                "  WARN: Sites.ReadWrite.All not granted — see SETUP_SHAREPOINT_WRITE.md. "
                "Read direction continues; push skipped.",
                file=sys.stderr,
            )
            return {"updated": updated, "created": created, "failed": failed, "skipped": skipped, "permission_denied": True}
        if ok:
            updated += 1
            print(f"  pushed {job_id}: {list(to_update.keys())}", flush=True)
        else:
            failed += 1

    # 2) Create new SharePoint rows for app-added customJobs
    for job in custom_jobs:
        jid = job.get("JobID")
        if not jid or jid in tombstones:
            skipped += 1
            continue
        if jid in sp_items_by_jobid:
            continue  # already in SP
        fields = {
            "JobID": jid,
            "PropertyID": job.get("PropertyID"),
            "PropertyNickname": job.get("PropertyNickname"),
            "ScheduledDate": job.get("ScheduledDate"),
            "SessionNumber": job.get("SessionNumber"),
            "Status": job.get("Status", "Pending"),
            "TypeID": job.get("TypeID"),
            "ContractorName": job.get("ContractorName"),
        }
        fields = {k: v for k, v in fields.items() if v not in (None, "")}
        ok, denied = post_item(fields)
        if denied:
            print(
                "  WARN: Sites.ReadWrite.All not granted — see SETUP_SHAREPOINT_WRITE.md.",
                file=sys.stderr,
            )
            return {"updated": updated, "created": created, "failed": failed, "skipped": skipped, "permission_denied": True}
        if ok:
            created += 1
            print(f"  created {jid} in SharePoint", flush=True)
        else:
            failed += 1

    return {"updated": updated, "created": created, "failed": failed, "skipped": skipped, "permission_denied": False}


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

    # === PUSH direction: apply events.json overlays back to SharePoint ===
    print("Reading events.json overlays + customJobs…", flush=True)
    events_data = read_events_json()
    overlay_count = len(events_data.get("events") or {})
    custom_count = len(events_data.get("customJobs") or [])
    print(f"  overlays: {overlay_count}, customJobs: {custom_count}", flush=True)

    if overlay_count or custom_count:
        items_by_jobid = {
            it.get("fields", {}).get("JobID"): it
            for it in items
            if it.get("fields", {}).get("JobID")
        }
        print("Pushing overlays + customJobs back to SharePoint…", flush=True)
        push_res = push_overlays_to_sharepoint(token, site_id, list_id, items_by_jobid, events_data)
        print(
            f"  push result: {push_res['updated']} updated, {push_res['created']} created, "
            f"{push_res['failed']} failed, {push_res['skipped']} skipped",
            flush=True,
        )
        # Re-fetch so data.json reflects the post-push SharePoint state.
        if (push_res["updated"] or push_res["created"]) and not push_res.get("permission_denied"):
            print("Re-fetching Schedule after push…", flush=True)
            items = graph_all(
                token,
                f"/sites/{site_id}/lists/{list_id}/items",
                params={"$expand": "fields", "$top": "100"},
            )
            print(f"  got {len(items)} items (post-push)", flush=True)
    else:
        print("  no overlays or customJobs to push.", flush=True)

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
