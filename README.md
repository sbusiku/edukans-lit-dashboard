# Edukans LiT — live KoBo sync

This makes the dashboard pull live from your KoboToolbox form instead of a static snapshot. A scheduled GitHub Action re-fetches your form's submissions every 15 minutes and writes them to `data/activities.json`, which the dashboard reads directly (GitHub's raw file host sends CORS headers that allow this — KoBo's own API doesn't).

## 1. Create a repo

Create a new **public** repo on GitHub (private works too, but then `raw.githubusercontent.com` needs a token in every request, which you don't want sitting in a client-side dashboard — public is simpler for this use case). Push the contents of this folder to it:

```
.github/workflows/sync-kobo.yml
scripts/sync_kobo.py
scripts/field_map.json
data/activities.json
```

## 2. Get a KoBo API token

In KoboToolbox: **Account Settings → Security → API Token** (or visit `https://kf.kobotoolbox.org/token/?format=json` while logged in). Copy the token.

## 3. Add it as a repo secret

In your new GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**
- Name: `KOBO_API_TOKEN`
- Value: the token from step 2

## 4. Verify the field mapping (important — do this once)

Your xlsx export uses friendly labels ("Date of Activity"), but the API returns the underlying XLSForm field names, which are usually different. Run this locally once:

```bash
export KOBO_API_TOKEN=your_token_here
python scripts/sync_kobo.py --inspect
```

This prints every field name in one real submission. Open `scripts/field_map.json` and make sure each value on the right matches an actual key from that output — fix any that don't match (grouped questions look like `group_name/question_name`). Commit the corrected `field_map.json`.

## 5. Run it

- **Manually**: go to the repo's **Actions** tab → "Sync KoBo activity data" → **Run workflow**.
- **On schedule**: it also runs automatically every 15 minutes once it's on the default branch.

Each run overwrites `data/activities.json` with the latest submissions and commits it if anything changed.

## 6. Point the dashboard at it

Open the dashboard HTML file, and in the **"Live data source"** box near the top, paste:

```
https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/data/activities.json
```

Click **Connect**. The dashboard fetches immediately, then polls every 60 seconds for changes while the page is open. If the fetch ever fails, it keeps showing the last good data and flags the error in the sync status line — it never silently goes blank.

## Notes

- Adjust the cron schedule in `sync-kobo.yml` if 15 minutes is too frequent or not frequent enough (GitHub won't reliably run more often than every ~5 min anyway).
- If your form is edited later (fields renamed/added), re-run `--inspect` and update `field_map.json`.
- The dashboard's own 60-second poll is separate from the sync interval — it's just checking whether the file on GitHub changed, not calling KoBo directly.
