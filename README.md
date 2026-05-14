# Old Well Mowing Dashboard

Live mowing dashboard for the Old Well properties team. Reads from a JSON snapshot exported every 15 min from SharePoint.

🔗 **Live URL:** https://emw-oldwell.github.io/oldwell-mowing-dashboard/

## How it works

- `index.html` — the dashboard. Pure HTML + CSS + JS, no build step.
- `data.json` — current snapshot of the SharePoint `Schedule` list. Updated every 15 min by a Power Automate flow named **"Sync Schedule to GitHub"**.
- Photos still live in SharePoint (`/sites/Mowing/Lists/Schedule/Attachments/*`). They load cross-origin from oldwellco.sharepoint.com — viewers need to be signed in to SharePoint at least once per session for thumbnails to render.

## Manual data refresh

If you need to force a refresh of `data.json` (without waiting 15 min for the next cron tick):
1. Open the **Sync Schedule to GitHub** flow in Power Automate
2. Click **Run** at the top
3. Wait ~30s, then reload the dashboard

## Local preview

```bash
cd /path/to/repo
python3 -m http.server 8000
# open http://localhost:8000
```

Photos won't render in local preview (cross-origin to oldwellco.sharepoint.com requires SP cookies that don't attach from localhost). Everything else does.

## Architecture

```
SharePoint Schedule list  ───▶  Power Automate (every 15 min)  ───▶  PUT data.json to GitHub  ───▶  GitHub Pages
                                                                                                       │
                                                                                                       ▼
                                                                                              browser (any team member)
```
