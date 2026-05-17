#!/usr/bin/env python3
"""
Generate CSV exports for office use.

Reads data.json (schedule from SharePoint) + events.json (app overlays) and
writes joined views to exports/:
  - schedule.csv   one row per job, with effective status/notes/photos
  - properties.csv per-property rollup (last done, next due, age, totals)
  - crews.csv      per-crew rollup (counts, properties covered, next job)

Wired into .github/workflows/sync.yml — runs every 15 min after sync.py.

Office workflow: open Excel, Data > From Web, paste the raw GitHub URL of
schedule.csv. Excel refreshes the table on open. Same for properties/crews.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data.json"
EVENTS = REPO_ROOT / "events.json"
EXPORTS_DIR = REPO_ROOT / "exports"


def load_events() -> dict:
    if not EVENTS.exists():
        return {"events": {}, "customJobs": [], "tombstones": {}}
    try:
        with open(EVENTS) as f:
            j = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"events": {}, "customJobs": [], "tombstones": {}}
    j.setdefault("events", {})
    j.setdefault("customJobs", [])
    j.setdefault("tombstones", {})
    return j


def effective(job: dict, ov: dict | None) -> dict:
    """Apply the overlay to a base job — mirrors the app's effective* helpers."""
    e = dict(job)
    if not ov:
        return e
    if ov.get("rescheduledTo"):
        e["ScheduledDate"] = ov["rescheduledTo"]
    if ov.get("reassignedTo"):
        e["ContractorName"] = ov["reassignedTo"]
    if ov.get("typeOverride"):
        e["TypeID"] = ov["typeOverride"]
    if ov.get("cancelled"):
        e["Status"] = "Cancelled"
    elif ov.get("status") in ("Done", "InProgress", "OnWay"):
        e["Status"] = ov["status"]
    elif ov.get("finishedAt"):
        e["Status"] = "Done"
    return e


def collect_photo_urls(job: dict, ov: dict | None) -> list[str]:
    urls = []
    for att in (job.get("Attachments") or []):
        u = att.get("ServerRelativeUrl")
        if u and isinstance(u, str):
            urls.append(u)
    for p in ((ov or {}).get("photos") or []):
        u = p.get("url")
        if u and isinstance(u, str):
            urls.append(u)
    return urls


def safe_text(s) -> str:
    """Flatten newlines/tabs so the CSV stays single-line per record."""
    return str(s or "").replace("\r", " ").replace("\n", " / ").replace("\t", " ")


def parse_iso_date(s: str | None):
    if not s:
        return None
    try:
        # Accept full ISO timestamps or bare YYYY-MM-DD
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def write_schedule_csv(jobs: list[dict], events_map: dict, tombstones: dict) -> tuple[Path, int]:
    fields = [
        "JobID", "PropertyID", "PropertyNickname", "TypeID",
        "ContractorName", "ScheduledDate", "Status", "SessionNumber",
        "IsCustomJob", "Rescheduled", "Reassigned", "TypeOverridden", "Cancelled",
        "OriginalScheduledDate", "OriginalContractor", "OriginalType",
        "OnMyWayAt", "StartedAt", "FinishedAt",
        "FinishedLat", "FinishedLng",
        "PhotoCount", "PhotoUrls",
        "CrewNotes", "OfficeNotes",
        "UpdatedBy", "LastUpdate",
    ]
    path = EXPORTS_DIR / "schedule.csv"
    written = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for job in jobs:
            jid = job.get("JobID")
            if not jid or jid in tombstones:
                continue
            ov = events_map.get(jid) or {}
            eff = effective(job, ov)
            photo_urls = collect_photo_urls(job, ov)
            w.writerow({
                "JobID": jid,
                "PropertyID": job.get("PropertyID", "") or "",
                "PropertyNickname": job.get("PropertyNickname", "") or "",
                "TypeID": eff.get("TypeID", "") or "",
                "ContractorName": eff.get("ContractorName", "") or "",
                "ScheduledDate": eff.get("ScheduledDate", "") or "",
                "Status": eff.get("Status", "") or "",
                "SessionNumber": job.get("SessionNumber", "") or "",
                "IsCustomJob": "yes" if (isinstance(job.get("ID"), int) and job["ID"] < 0) else "",
                "Rescheduled": "yes" if ov.get("rescheduledTo") else "",
                "Reassigned": "yes" if ov.get("reassignedTo") else "",
                "TypeOverridden": "yes" if ov.get("typeOverride") else "",
                "Cancelled": "yes" if ov.get("cancelled") else "",
                "OriginalScheduledDate": job.get("ScheduledDate", "") if ov.get("rescheduledTo") else "",
                "OriginalContractor": job.get("ContractorName", "") if ov.get("reassignedTo") else "",
                "OriginalType": job.get("TypeID", "") if ov.get("typeOverride") else "",
                "OnMyWayAt": ov.get("onMyWayAt", "") or "",
                "StartedAt": ov.get("startedAt", "") or "",
                "FinishedAt": ov.get("finishedAt", "") or "",
                "FinishedLat": ov.get("finishedLat", "") or "",
                "FinishedLng": ov.get("finishedLng", "") or "",
                "PhotoCount": len(photo_urls),
                "PhotoUrls": "; ".join(photo_urls),
                "CrewNotes": safe_text(ov.get("notes")),
                "OfficeNotes": safe_text(ov.get("officeNote")),
                "UpdatedBy": ov.get("updatedBy", "") or "",
                "LastUpdate": ov.get("lastUpdate", "") or "",
            })
            written += 1
    return path, written


def write_properties_csv(jobs_eff: list[dict]) -> tuple[Path, int]:
    by_prop: dict[str, list[dict]] = defaultdict(list)
    for j in jobs_eff:
        key = j.get("PropertyID") or j.get("PropertyNickname") or "?"
        by_prop[key].append(j)
    fields = [
        "PropertyID", "PropertyNickname", "TypeID", "DefaultContractor",
        "TotalJobs", "DoneJobs", "PendingJobs", "CancelledJobs",
        "LastDoneDate", "NextScheduledDate", "DaysSinceLastDone",
    ]
    path = EXPORTS_DIR / "properties.csv"
    today = datetime.now(timezone.utc).date()
    written = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        keys_sorted = sorted(by_prop.keys(), key=lambda k: (by_prop[k][0].get("PropertyNickname") or "").upper())
        for key in keys_sorted:
            group = by_prop[key]
            sample = group[0]
            done = [j for j in group if j.get("Status") == "Done"]
            pending = [j for j in group if j.get("Status") == "Pending"]
            cancelled = [j for j in group if j.get("Status") == "Cancelled"]
            done_dates = sorted(d for d in (parse_iso_date(j.get("ScheduledDate")) for j in done) if d)
            pending_future = sorted(d for d in (parse_iso_date(j.get("ScheduledDate")) for j in pending) if d and d >= today)
            last_done = done_dates[-1].isoformat() if done_dates else ""
            next_sched = pending_future[0].isoformat() if pending_future else ""
            days_since = (today - done_dates[-1]).days if done_dates else ""
            w.writerow({
                "PropertyID": sample.get("PropertyID", "") or "",
                "PropertyNickname": sample.get("PropertyNickname", "") or "",
                "TypeID": sample.get("TypeID", "") or "",
                "DefaultContractor": sample.get("ContractorName", "") or "",
                "TotalJobs": len(group),
                "DoneJobs": len(done),
                "PendingJobs": len(pending),
                "CancelledJobs": len(cancelled),
                "LastDoneDate": last_done,
                "NextScheduledDate": next_sched,
                "DaysSinceLastDone": days_since,
            })
            written += 1
    return path, written


def write_crews_csv(jobs_eff: list[dict]) -> tuple[Path, int]:
    by_crew: dict[str, list[dict]] = defaultdict(list)
    for j in jobs_eff:
        name = j.get("ContractorName") or "(unassigned)"
        by_crew[name].append(j)
    fields = [
        "ContractorName", "TotalJobs", "DoneJobs", "PendingJobs",
        "PropertiesCovered", "NextScheduledDate",
    ]
    path = EXPORTS_DIR / "crews.csv"
    today = datetime.now(timezone.utc).date()
    written = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for crew in sorted(by_crew.keys()):
            group = by_crew[crew]
            done = [j for j in group if j.get("Status") == "Done"]
            pending = [j for j in group if j.get("Status") == "Pending"]
            pending_future = sorted(d for d in (parse_iso_date(j.get("ScheduledDate")) for j in pending) if d and d >= today)
            props = {(j.get("PropertyID") or j.get("PropertyNickname") or "?") for j in group}
            w.writerow({
                "ContractorName": crew,
                "TotalJobs": len(group),
                "DoneJobs": len(done),
                "PendingJobs": len(pending),
                "PropertiesCovered": len(props),
                "NextScheduledDate": pending_future[0].isoformat() if pending_future else "",
            })
            written += 1
    return path, written


def main() -> int:
    if not DATA.exists():
        print("ERROR: data.json missing — run sync.py first", file=sys.stderr)
        return 1
    with open(DATA) as f:
        data = json.load(f)
    events = load_events()
    events_map = events["events"]
    tombstones = events["tombstones"]
    custom_jobs = events["customJobs"]

    all_jobs = list(data.get("jobs") or []) + list(custom_jobs)

    jobs_eff = []
    for job in all_jobs:
        jid = job.get("JobID")
        if not jid or jid in tombstones:
            continue
        jobs_eff.append(effective(job, events_map.get(jid)))

    EXPORTS_DIR.mkdir(exist_ok=True)
    s_path, s_n = write_schedule_csv(all_jobs, events_map, tombstones)
    p_path, p_n = write_properties_csv(jobs_eff)
    c_path, c_n = write_crews_csv(jobs_eff)
    print(f"Wrote {s_path.relative_to(REPO_ROOT)} ({s_n} rows)")
    print(f"Wrote {p_path.relative_to(REPO_ROOT)} ({p_n} rows)")
    print(f"Wrote {c_path.relative_to(REPO_ROOT)} ({c_n} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
