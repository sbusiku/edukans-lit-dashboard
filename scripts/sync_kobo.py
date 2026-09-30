#!/usr/bin/env python3
"""
sync_kobo.py
------------
Pulls submissions from KoboToolbox forms feeding the Edukans LiT "Programme
Console" dashboard, plus a frozen local baseline, and merges them into one
flat JSON file (data/activities.json). The Training tab is fed by three
sources that all share activity_type "Training" so their totals add
together: training_legacy (a static, locally-stored snapshot of the 49
historical submissions from the now-retired original training form -- never
fetched from KoBo, always included), local_govt, and bog_pta. Each carries a
training_type ("Legacy" / "Local Govt" / "BoG/PTA") that powers the tab's
dropdown filter. Radio Talkshow, Community Dialogue, and Back to School are
each a single live form. The dashboard splits everything back out by
`activity_type`, so this script's job is: load or fetch each source, map its
fields onto the dashboard's common schema, tag it, and combine.

Auth:   reads KOBO_API_TOKEN from the environment (set as a GitHub secret).
        A single token covers all live forms as long as they're on the same
        KoBoToolbox account. Not needed for the static training_legacy
        source.
Source: KOBO_SERVER (default https://kf.kobotoolbox.org) plus the per-form
        asset UIDs and field mappings in scripts/forms.json.

Modes:
  python sync_kobo.py --inspect <key>   -> prints the raw keys of one
                                            submission for that form (key is
                                            "local_govt", "bog_pta", "radio",
                                            "dialogue", or "backtoschool") so
                                            you can correct forms.json's
                                            field_map. For the static
                                            "training_legacy" key it just
                                            confirms there's nothing to fetch.
  python sync_kobo.py                   -> full sync of every source in
                                            forms.json into data/activities.json
  python sync_kobo.py --only bog_pta    -> sync just one source (for
                                            testing; merges into the
                                            existing file without touching
                                            other sources, including the
                                            legacy baseline)
"""
import json
import os
import sys
import urllib.request
import urllib.error

KOBO_SERVER = os.environ.get("KOBO_SERVER", "https://kf.kobotoolbox.org")
KOBO_API_TOKEN = os.environ.get("KOBO_API_TOKEN", "")

HERE = os.path.dirname(os.path.abspath(__file__))
FORMS_PATH = os.path.join(HERE, "forms.json")
OUTPUT_PATH = os.path.join(HERE, "..", "data", "activities.json")


def fetch_submissions(asset_uid):
    if not KOBO_API_TOKEN:
        print("ERROR: KOBO_API_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    url = f"{KOBO_SERVER}/api/v2/assets/{asset_uid}/data.json"
    req = urllib.request.Request(url, headers={"Authorization": f"Token {KOBO_API_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"ERROR: KoBo API returned {e.code} for asset {asset_uid}: {e.reason}", file=sys.stderr)
        print(e.read().decode("utf-8", errors="ignore"), file=sys.stderr)
        sys.exit(1)

    return payload.get("results", payload if isinstance(payload, list) else [])


def num(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def get_path(record, dotted_key):
    """Supports simple dotted/grouped keys, e.g. 'group_x/question_y'."""
    if not dotted_key:
        return None
    if dotted_key in record:
        return record[dotted_key]
    alt = dotted_key.replace("/", "_")
    if alt in record:
        return record[alt]
    return None


def parse_geopoint(raw):
    """KoBo geopoint fields come back as 'lat lon alt acc' as one string."""
    if not raw:
        return None, None
    try:
        parts = str(raw).split()
        return float(parts[0]), float(parts[1])
    except (ValueError, IndexError):
        return None, None


def transform(record, fmap, activity_type, training_type=None):
    date_raw = get_path(record, fmap.get("date"))
    lat, lon = parse_geopoint(get_path(record, fmap.get("geopoint")))
    out = {
        "date": (date_raw or "")[:10],
        "region": get_path(record, fmap.get("region")) or "Unknown",
        "district": get_path(record, fmap.get("district")) or "Unknown",
        "subcounty": get_path(record, fmap.get("subcounty")) or "",
        "venue": get_path(record, fmap.get("venue")) or "",
        "activity_type": activity_type,
        "description": (get_path(record, fmap.get("description")) or "")[:220],
        "participant_category": get_path(record, fmap.get("participant_category")) or "",
        "male": num(get_path(record, fmap.get("male"))),
        "female": num(get_path(record, fmap.get("female"))),
        "pwd_male": num(get_path(record, fmap.get("pwd_male"))),
        "pwd_female": num(get_path(record, fmap.get("pwd_female"))),
        "total": num(get_path(record, fmap.get("total"))),
        "feedback": (get_path(record, fmap.get("feedback")) or "")[:220],
        "challenges": (get_path(record, fmap.get("challenges")) or "")[:220],
        "recommendations": (get_path(record, fmap.get("recommendations")) or "")[:220],
        "officer": get_path(record, fmap.get("officer")) or "Unknown",
        "lat": lat,
        "lon": lon,
        "submitted": (get_path(record, fmap.get("submission_time")) or "").replace("T", " ")[:16],
    }
    if training_type:
        out["training_type"] = training_type
    return out


def load_forms():
    with open(FORMS_PATH) as f:
        cfg = json.load(f)
    return cfg["forms"]


def load_static_records(static_file):
    path = os.path.join(HERE, static_file)
    with open(path) as f:
        return json.load(f)


def cmd_inspect(key):
    forms = {f["key"]: f for f in load_forms()}
    if key not in forms:
        print(f"Unknown form key '{key}'. Valid keys: {', '.join(forms)}", file=sys.stderr)
        sys.exit(1)
    form = forms[key]
    if form.get("static"):
        print(f"'{key}' is a static source loaded from {form['static_file']} -- there is nothing to fetch or inspect on KoBo for it.")
        return
    records = fetch_submissions(form["asset_uid"])
    if not records:
        print(f"No submissions returned yet for '{key}' ({form['asset_uid']}).")
        return
    print(f"Fetched {len(records)} submissions for '{key}'. Keys in the first record:\n")
    for k in sorted(records[0].keys()):
        print(f"  {k!r}: {records[0][k]!r}"[:160])
    print(f"\nCopy the relevant keys into scripts/forms.json under the '{key}' form's field_map.")


def cmd_sync(only=None):
    forms = load_forms()
    if only:
        forms = [f for f in forms if f["key"] == only]
        if not forms:
            print(f"Unknown form key '{only}'.", file=sys.stderr)
            sys.exit(1)

    all_records = []
    counts = {}
    for form in forms:
        key = form["key"]
        if form.get("static"):
            transformed = load_static_records(form["static_file"])
            # make sure activity_type/training_type match forms.json even if
            # the frozen file drifts, so config stays the single source of truth
            for r in transformed:
                r["activity_type"] = form["activity_type"]
                if form.get("training_type"):
                    r["training_type"] = form["training_type"]
        else:
            records = fetch_submissions(form["asset_uid"])
            transformed = [transform(r, form["field_map"], form["activity_type"], form.get("training_type")) for r in records]
        all_records.extend(transformed)
        counts[key] = len(transformed)
        print(f"  {key:16s} ({form['activity_type']:20s}) -> {len(transformed)} records")

    if only:
        # merge into existing file, replacing just this form's own records
        # (matched on activity_type + training_type where applicable, so
        # syncing --only bog_pta doesn't wipe out local_govt's or the legacy
        # baseline's rows, since all three share activity_type "Training")
        existing = []
        if os.path.exists(OUTPUT_PATH):
            with open(OUTPUT_PATH) as f:
                existing = json.load(f)
        form = forms[0]
        activity_type = form["activity_type"]
        training_type = form.get("training_type")

        def is_same_source(r):
            if r.get("activity_type") != activity_type:
                return False
            if training_type:
                return r.get("training_type") == training_type
            return True

        existing = [r for r in existing if not is_same_source(r)]
        all_records = existing + all_records

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(all_records, f, separators=(",", ":"))

    print(f"\nWrote {len(all_records)} total records to {OUTPUT_PATH}")
    print("Breakdown:", counts)


def main():
    args = sys.argv[1:]
    if "--inspect" in args:
        idx = args.index("--inspect")
        if idx + 1 >= len(args):
            print("Usage: sync_kobo.py --inspect <key>", file=sys.stderr)
            sys.exit(1)
        cmd_inspect(args[idx + 1])
        return

    only = None
    if "--only" in args:
        idx = args.index("--only")
        if idx + 1 >= len(args):
            print("Usage: sync_kobo.py --only <key>", file=sys.stderr)
            sys.exit(1)
        only = args[idx + 1]

    cmd_sync(only=only)


if __name__ == "__main__":
    main()
