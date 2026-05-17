# Activating cross-device sync

The app code is shipped. To turn on real cloud sync (crew changes show up on
office's phone within 30s), you need to do three one-time clicks:

## 1. Create a GitHub Personal Access Token

The Vercel function uses this token to commit events + photos back to this repo.

1. Go to https://github.com/settings/personal-access-tokens/new (fine-grained token)
2. **Name**: `oldwell-mowing-app-bot`
3. **Expiration**: 1 year (or as long as you're comfortable)
4. **Resource owner**: `emw-oldwell`
5. **Repository access**: Only select repositories → `oldwell-mowing-dashboard`
6. **Permissions** → Repository permissions:
   - **Contents**: Read and write
   - **Metadata**: Read-only (auto-selected)
7. Click **Generate token**. Copy the token (`github_pat_…`). You'll paste it
   into Vercel in step 3.

## 2. Import this repo into Vercel

1. Go to https://vercel.com/new
2. Pick the `Matthew_Dad` team
3. **Import Git Repository** → find `emw-oldwell/oldwell-mowing-dashboard`
4. Project name: `oldwell-mowing-api` (suggested)
5. Framework Preset: **Other** (Vercel will auto-detect the `api/` folder)
6. **Do not change the Build / Output settings.** The repo has no build step —
   Vercel will just deploy the serverless functions in `api/`.
7. Click **Deploy**. First deploy will succeed but the functions will return
   500 ("server not configured") until step 3.

## 3. Add the env var, redeploy

1. Open the new Vercel project → **Settings** → **Environment Variables**
2. Add:
   - Key: `GITHUB_TOKEN`
   - Value: paste the token from step 1
   - Environments: all three (Production / Preview / Development)
3. (Optional) Add `GITHUB_REPO=emw-oldwell/oldwell-mowing-dashboard` —
   only needed if you fork or rename.
4. Go to **Deployments** → latest → **⋯ menu** → **Redeploy** (use existing build cache).

## 4. Tell Claude the Vercel URL

Once redeployed, copy the production URL (e.g. `https://oldwell-mowing-api.vercel.app`)
and paste it back in chat. I'll wire it into the app and we'll smoke-test it
together.

## How to verify it's working (optional)

After step 3, this should return 405 (POST-only) not 500:

    curl -i https://YOUR-VERCEL-URL.vercel.app/api/event

And this should return 405:

    curl -i https://YOUR-VERCEL-URL.vercel.app/api/photo

If you see 500 with `"GITHUB_TOKEN env var not set"`, the env var didn't take
— make sure you redeployed after adding it.

## Costs

- Vercel: free tier covers this easily. ~5 contractors × ~50 events/day is
  negligible vs. Vercel's 100GB-hours/month free.
- GitHub: API rate limit is 5,000 requests/hour authenticated. Far above
  typical usage.
- Repo size: 1280px photos at JPEG 0.82 quality are ~300KB each. Adding 100
  photos/week = ~30MB/week, ~1.5GB/year. Well under GitHub's soft 5GB cap.
  If we ever need to slim down, migrate photos to Vercel Blob in v2.

## Reverting

To turn sync back off (keep app working in localStorage-only mode), comment out
or remove the `window.OLDWELL_SYNC_BASE = '...'` line in `app/index.html`.
The app gracefully degrades.

To shut sync down entirely: delete the Vercel project + revoke the PAT.
