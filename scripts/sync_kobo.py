#!/usr/bin/env python3
"""
sync_kobo.py
------------
Pulls submissions from a KoboToolbox form via the API v2 and writes a flat
JSON file (data/activities.json) shaped for the Edukans LiT dashboard.

Auth:   reads KOBO_API_TOKEN from the environment (set as a GitHub secret).
Source: KOBO_SERVER + KOBO_ASSET_UID (also env vars, with defaults below).

Two modes:
  python sync_kobo.py --inspect      -> prints the raw keys of one submission
                                         so you can fill in field_map.json
  python sync_kobo.py                -> full sync using field_map.json

field_map.json lets you map the dashboard's expected fields to your form's
actual field names (the XLSForm "name" column, not the label), without
touching this script. Run --inspect once, look at the printed keys, and
fill in field_map.json accordingly. Sensible defaults/guesses are already
in field_map.json based on your export's column labels -- verify them.
"""
import json
import os
import sys
import urllib.request
import urllib.error

KOBO_SERVER = os.environ.get("KOBO_SERVER", "https://kf.kobotoolbox.org")
KOBO_ASSET_UID = os.environ.get("KOBO_ASSET_UID", "ahBUh4eZMqYjdWoBujKmZW")
KOBO_API_TOKEN = os.environ.get("KOBO_API_TOKEN", "")

DATA_URL = f"{KOBO_SERVER}/api/v2/assets/{KOBO_ASSET_UID}/data.json"

HERE = os.path.dirname(os.path.abspath(__file__))
FIELD_MAP_PATH = os.path.join(HERE, "field_map.json")
OUTPUT_PATH = os.path.join(HERE, "..", "data", "activities.json")


def fetch_submissions():
    if not KOBO_API_TOKEN:
        print("ERROR: KOBO_API_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    req = urllib.request.Request(
        DATA_URL,
        headers={"Authorization": f"Token {KOBO_API_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"ERROR: KoBo API returned {e.code}: {e.reason}", file=sys.stderr)
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
    if dotted_key is None:
        return None
    if dotted_key in record:
        return record[dotted_key]
    # try replacing / with _ or vice versa, KoBo sometimes flattens groups differently
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
        lat, lon = float(parts[0]), float(parts[1])
        return lat, lon
    except (ValueError, IndexError):
        return None, None


def main():
    if "--inspect" in sys.argv:
        records = fetch_submissions()
        if not records:
            print("No submissions returned.")
            return
        print(f"Fetched {len(records)} submissions. Keys in the first record:\n")
        for k in sorted(records[0].keys()):
            print(f"  {k!r}: {records[0][k]!r}"[:160])
        print(
            "\nCopy the relevant keys into scripts/field_map.json, "
            "then re-run without --inspect."
        )
        return

    with open(FIELD_MAP_PATH) as f:
        fmap = json.load(f)

    records = fetch_submissions()
    out = []
    for r in records:
        date_raw = get_path(r, fmap.get("date"))
        lat, lon = parse_geopoint(get_path(r, fmap.get("geopoint")))

        out.append({
            "date": (date_raw or "")[:10],
            "region": get_path(r, fmap.get("region")) or "Unknown",
            "district": get_path(r, fmap.get("district")) or "Unknown",
            "subcounty": get_path(r, fmap.get("subcounty")) or "",
            "venue": get_path(r, fmap.get("venue")) or "",
            "activity_type": get_path(r, fmap.get("activity_type")) or "Training",
            "description": (get_path(r, fmap.get("description")) or "")[:220],
            "participant_category": get_path(r, fmap.get("participant_category")) or "",
            "male": num(get_path(r, fmap.get("male"))),
            "female": num(get_path(r, fmap.get("female"))),
            "pwd_male": num(get_path(r, fmap.get("pwd_male"))),
            "pwd_female": num(get_path(r, fmap.get("pwd_female"))),
            "total": num(get_path(r, fmap.get("total"))),
            "feedback": (get_path(r, fmap.get("feedback")) or "")[:220],
            "challenges": (get_path(r, fmap.get("challenges")) or "")[:220],
            "recommendations": (get_path(r, fmap.get("recommendations")) or "")[:220],
            "officer": get_path(r, fmap.get("officer")) or "Unknown",
            "lat": lat,
            "lon": lon,
            "submitted": (get_path(r, fmap.get("submission_time")) or "").replace("T", " ")[:16],
        })

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(out, f, indent=None, separators=(",", ":"))

    print(f"Wrote {len(out)} records to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
