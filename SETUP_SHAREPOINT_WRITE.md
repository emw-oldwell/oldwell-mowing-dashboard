# Activating two-way SharePoint sync

The sync workflow already reads SharePoint. To also **write app changes back**
to the SharePoint Schedule list (mark-done, reschedule, reassign, type
override, new app-added jobs), upgrade the Azure AD app's permission scope.

The code is already deployed. Until you complete the steps below, the push
direction logs a "permission not granted" hint and the read direction keeps
working as it did before. Nothing breaks while you decide whether to
upgrade.

## Steps

### 1. Open the Azure AD app

1. Go to https://portal.azure.com → **Azure Active Directory** → **App registrations**
2. Find **OldWell Mowing Dashboard Sync** (the app whose Client ID is stored as `AZURE_CLIENT_ID` in this repo's GitHub secrets)
3. Click into it.

### 2. Add the write permission

1. Left sidebar: **API permissions**
2. Click **+ Add a permission** → **Microsoft Graph** → **Application permissions**
3. Search for `Sites.ReadWrite.All`
4. Check the box → **Add permissions**

You'll now see `Sites.ReadWrite.All` in the list with a warning icon (Not granted for {tenant}).

### 3. Grant admin consent

1. At the top of the permissions list, click **Grant admin consent for Old Well** (this button is only enabled because you're tenant admin).
2. Confirm.
3. The warning icon turns into a green check ✓.

That's it on the Azure side.

### 4. Trigger a sync run

Either:

- Wait up to 15 min for the scheduled run, OR
- Open https://github.com/emw-oldwell/oldwell-mowing-dashboard/actions/workflows/sync.yml → **Run workflow** → main → **Run workflow**.

When you watch the run, look for log lines like:

```
Reading events.json overlays + customJobs…
  overlays: 3, customJobs: 25
Pushing overlays + customJobs back to SharePoint…
  pushed J-0019: ['Status']
  created JX-P-013-01 in SharePoint
  push result: 3 updated, 25 created, 0 failed, 0 skipped
Re-fetching Schedule after push…
```

If you instead see:

```
  WARN: Sites.ReadWrite.All not granted — see SETUP_SHAREPOINT_WRITE.md
```

The consent didn't take — double-check step 3 (green check next to `Sites.ReadWrite.All` in Azure).

## What gets pushed

| App overlay         | SharePoint Schedule field |
|---------------------|---------------------------|
| `status: Done` or `finishedAt` set | `Status = "Done"` |
| `rescheduledTo`     | `ScheduledDate`           |
| `reassignedTo`      | `ContractorName`          |
| `typeOverride`      | `TypeID`                  |
| `customJobs[]`      | New rows in the Schedule list |

## What does NOT get pushed (v1 trade-offs)

- **Cancelled / tombstoned jobs** — SharePoint keeps the original row. App hides them. If you want SharePoint to reflect a cancel, mark the row manually in SharePoint.
- **In-progress / on-the-way status** — only `Done` propagates. Intermediate states stay app-only.
- **Crew notes, office notes, crew photos** — these don't have corresponding SharePoint columns. Crew photos live in `/photos/` in this repo (raw URLs); notes live in `events.json`.
- **Crew records** (the new "Add crew" form) — crews were intentionally outside SharePoint per the original architecture; the app's `events.json` is canonical.

## Conflict model

**App overlays win.** If anyone edits the SharePoint Schedule list directly for a field that already has an app overlay, the next sync run will overwrite that edit. Tell office to make schedule changes in the app (`/app/`), not in SharePoint directly.

**SP edits to fields with NO overlay are safe.** Example: a row's `Status` is "Pending" with no app override → office can change it in SP to anything and the next pull picks it up.

If you want to make a SharePoint change persistent for a job that has an active overlay: first delete that overlay in the app (`Delete this entry` on the office job sheet), then make the SP edit. The tombstone tells the app to forget the overlay; the next sync sees only the SP value.

## Reverting

To turn write-back off, remove `Sites.ReadWrite.All` from the Azure app's API permissions and click **Remove admin consent**. The workflow falls back to the read-only behavior automatically — no code change required.
