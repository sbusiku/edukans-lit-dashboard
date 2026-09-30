# Edukans LiT — live KoBo sync (Training = Legacy + Local Govt + BoG/PTA)

Feeds the "Programme Console" dashboard's four tabs. Training now combines
**three** sources whose numbers all **add together**:

1. **Legacy** — the 49 historical submissions from the original training
   form (`ahBUh4eZMqYjdWoBujKmZW`), which is retired and no longer queried.
   These are frozen into `scripts/baseline_training.json` and are included
   in **every single sync**, so that data is never lost even though the
   source form is gone.
2. **Local Govt** — live form `aikoPjLqyGm75ENGf6BiMw`.
3. **BoG/PTA** — live form `auSVXUyjyrc4GDvWKHZ8uG`.

A dropdown inside the Training tab lets you filter down to any one of the
three; left on "All tools" it shows all three combined. Radio Talkshow,
Community Dialogue and Back to School are unchanged, each a single form.

The dashboard needs no further changes — it already filters by
`activity_type`, and the Training tab reads a `training_type` field
("Legacy" / "Local Govt" / "BoG/PTA") to power its dropdown.

## How the legacy baseline works

`scripts/forms.json` has a `training_legacy` entry marked `"static": true`.
Unlike the other entries, it isn't fetched from KoBo at all — `sync_kobo.py`
just loads `scripts/baseline_training.json` straight off disk and includes
it in the output every time, whether you run a full sync or `--only
some_other_form`. You never need to do anything with it; it's there so the
1,120 participants and 49 sessions the old form recorded keep showing up
forever, even though that form is gone.

If you ever need to change the baseline itself (say, correcting a data
entry error from the old system), edit `scripts/baseline_training.json`
directly — each record already matches the dashboard's schema — and run any
sync; the file's `activity_type`/`training_type` fields are re-stamped from
`forms.json` on every run, so as long as those two entries in `forms.json`
stay `"Training"` / `"Legacy"` you're safe to edit everything else in that
file freely.

## The forms

| Tab | Source | Form UID | `activity_type` | `training_type` |
|---|---|---|---|---|
| Training | Legacy (frozen, no longer synced) | ~~`ahBUh4eZMqYjdWoBujKmZW`~~ | `Training` | `Legacy` |
| Training | Local Govt | `aikoPjLqyGm75ENGf6BiMw` | `Training` | `Local Govt` |
| Training | BoG/PTA | `auSVXUyjyrc4GDvWKHZ8uG` | `Training` | `BoG/PTA` |
| Radio Talkshow | — | `aMCKMhZ3xyVawPbUmqq7Gb` | `Radio Talkshow` | — |
| Community Dialogue | — | `aPTBQobNQNbHNTGmQCjEbF` | `Community Dialogue` | — |
| Back to School | — | `a9rJ5Lwk2rCosqGrHX25Q2` | `Back to School` | — |

Don't change the `activity_type` or `training_type` strings — the dashboard
matches on them exactly (case and spacing included).

## 1. Create a repo

Create a **public** GitHub repo (private works too, but then
`raw.githubusercontent.com` needs a token on every request — public is
simpler for a client-side dashboard). Push:

```
.github/workflows/sync-kobo.yml
scripts/sync_kobo.py
scripts/forms.json
scripts/baseline_training.json
data/activities.json
```

*(`data/activities.json` in this package is already seeded with the 49
legacy records, tagged correctly, so the Training tab shows real numbers
from the moment you connect it — even before Local Govt or BoG/PTA have any
submissions.)*

## 2. Get a KoBo API token

**Account Settings → Security → API Token** in KoboToolbox (or visit
`https://kf.kobotoolbox.org/token/?format=json` while logged in). One token
covers all live forms. Not needed for the legacy baseline.

## 3. Add it as a repo secret

**Settings → Secrets and variables → Actions → New repository secret**
- Name: `KOBO_API_TOKEN`
- Value: the token from step 2

## 4. Verify each live form's field mapping (do this once per form)

I don't have login access to KoBo, so `field_map` for `local_govt`, `bog_pta`,
`radio`, `dialogue`, and `backtoschool` in `forms.json` are placeholder
guesses. Run, per form:

```bash
export KOBO_API_TOKEN=your_token_here
python scripts/sync_kobo.py --inspect local_govt
python scripts/sync_kobo.py --inspect bog_pta
python scripts/sync_kobo.py --inspect radio
python scripts/sync_kobo.py --inspect dialogue
python scripts/sync_kobo.py --inspect backtoschool
```

(`--inspect training_legacy` also works, but just tells you it's a static
source with nothing to fetch — that's expected.)

Each prints one real submission's field names. Open `scripts/forms.json`
and fix any `field_map` values that don't match. Grouped questions look
like `group_name/question_name` — use that exact string as printed.

## 5. Run it

- **Manually**: repo's **Actions** tab → "Sync KoBo activity data" → **Run
  workflow**. Syncs everything, including the legacy baseline, in one go.
- **On schedule**: runs automatically every 15 minutes once merged to the
  default branch.
- **One source at a time** (useful while fixing mappings):
  `python scripts/sync_kobo.py --only local_govt` re-syncs just that form
  and merges it into the existing file — it only replaces that source's own
  rows, never the legacy baseline's or the other training source's, even
  though all three share `activity_type: "Training"`.

## 6. Point the dashboard at it

Paste into the **"Live data source"** field:

```
https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/data/activities.json
```

Click **Connect**. Training's totals will already include the 1,120
participants from the legacy baseline; Local Govt and BoG/PTA add on top of
that as their forms collect submissions.

## Notes

- If Local Govt or BoG/PTA look empty after connecting but Legacy shows up
  fine, check: the form has submissions, `field_map` matches the real field
  names, and `activity_type`/`training_type` match the table above exactly.
- Re-run `--inspect` any time a live form's questions change.
- The legacy baseline never needs re-inspecting — it's frozen data, not a
  live source.
