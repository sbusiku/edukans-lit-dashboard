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

Select-field resolution: for select_one/select_multiple questions (region,
district, participant category, etc.), KoBo's submission data only ever
contains the question's internal choice CODE, never the label shown on the
form -- so if a form's choice list was built with codes like "1", "6", "30"
instead of readable text, that's exactly what lands in the data, unresolved.
To fix this, the script also fetches each live form's own definition
(asset_schema) once per sync and builds a code -> label map per choice list,
then resolves every mapped field through it automatically. Nothing needs
configuring for this -- it just works off the form's own choice lists. If a
form's schema can't be fetched for some reason, that form's fields fall back
to raw codes rather than failing the sync.

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


def fetch_asset_schema(asset_uid):
    """
    Fetches the form's own definition (questions + choice lists) so select_one
    / select_multiple answers can be resolved from their raw stored code
    (e.g. "6") to the human-readable label (e.g. "Gulu"). KoBo's submission
    API never sends the label, only the code, so without this step any
    select-type question -- region and district included, on forms where
    whoever built the choice list used numeric codes -- comes through as
    numbers instead of names.

    Returns None on any failure (missing permission, network hiccup, etc.)
    rather than raising, so a form without a readable schema just falls back
    to raw passthrough instead of breaking the whole sync.
    """
    if not KOBO_API_TOKEN:
        return None
    url = f"{KOBO_SERVER}/api/v2/assets/{asset_uid}/?format=json"
    req = urllib.request.Request(url, headers={"Authorization": f"Token {KOBO_API_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        print(f"WARNING: couldn't fetch form schema for asset {asset_uid} ({e}); "
              f"select-type fields on this form will be left as raw codes.", file=sys.stderr)
        return None


def build_choice_resolver(schema):
    """
    From a fetched asset schema, builds:
      select_map:     { "group/question_name": ("one"|"multi", list_name) }
                       (also indexed by the "group_question_name" underscore
                       form, so it matches however field_map.json wrote it)
      choice_labels:  { list_name: { code: label } }
    Returns (select_map, choice_labels); both empty if schema is unusable.
    """
    select_map = {}
    choice_labels = {}
    if not schema or "content" not in schema:
        return select_map, choice_labels

    content = schema["content"]

    for choice in content.get("choices", []):
        list_name = choice.get("list_name")
        code = choice.get("name")
        label = choice.get("label")
        if isinstance(label, list):
            label = label[0] if label else code
        if list_name is None or code is None:
            continue
        choice_labels.setdefault(list_name, {})[str(code)] = label or code

    group_stack = []
    for item in content.get("survey", []):
        t = item.get("type", "")
        if t in ("begin_group", "begin_repeat"):
            group_stack.append(item.get("name", ""))
            continue
        if t in ("end_group", "end_repeat"):
            if group_stack:
                group_stack.pop()
            continue
        name = item.get("name")
        if not name:
            continue
        full_path = "/".join(group_stack + [name]) if group_stack else name
        alt_path = full_path.replace("/", "_")

        kind, list_name = None, None
        if t.startswith("select_one "):
            kind, list_name = "one", t[len("select_one "):].strip()
        elif t.startswith("select_multiple "):
            kind, list_name = "multi", t[len("select_multiple "):].strip()

        if kind:
            select_map[full_path] = (kind, list_name)
            select_map[alt_path] = (kind, list_name)

    return select_map, choice_labels


def resolve_value(raw, dotted_key, select_map, choice_labels):
    """Translates a select_one/select_multiple raw code (or space-separated
    codes, for select_multiple) into its label(s). Anything not recognised
    as a select-type field -- or any code with no matching label -- passes
    through unchanged, so this never turns a valid value into an error."""
    if raw is None or not dotted_key or dotted_key not in select_map:
        return raw
    kind, list_name = select_map[dotted_key]
    labels = choice_labels.get(list_name, {})
    if not labels:
        return raw
    if kind == "one":
        return labels.get(str(raw), raw)
    tokens = str(raw).split()
    return ", ".join(labels.get(t, t) for t in tokens)


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


def transform(record, fmap, activity_type, training_type=None, select_map=None, choice_labels=None):
    select_map = select_map or {}
    choice_labels = choice_labels or {}

    def field(key):
        dotted_key = fmap.get(key)
        raw = get_path(record, dotted_key)
        return resolve_value(raw, dotted_key, select_map, choice_labels)

    date_raw = field("date")
    lat, lon = parse_geopoint(field("geopoint"))
    out = {
        "date": (date_raw or "")[:10],
        "region": field("region") or "Unknown",
        "district": field("district") or "Unknown",
        "subcounty": field("subcounty") or "",
        "venue": field("venue") or "",
        "activity_type": activity_type,
        "description": (field("description") or "")[:220],
        "participant_category": field("participant_category") or "",
        "male": num(field("male")),
        "female": num(field("female")),
        "pwd_male": num(field("pwd_male")),
        "pwd_female": num(field("pwd_female")),
        "total": num(field("total")),
        "feedback": (field("feedback") or "")[:220],
        "challenges": (field("challenges") or "")[:220],
        "recommendations": (field("recommendations") or "")[:220],
        "officer": field("officer") or "Unknown",
        "lat": lat,
        "lon": lon,
        "submitted": (field("submission_time") or "").replace("T", " ")[:16],
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

    schema = fetch_asset_schema(form["asset_uid"])
    select_map, choice_labels = build_choice_resolver(schema)
    if select_map:
        print(f"\nSelect-type questions detected on this form (codes below are auto-resolved")
        print("to labels during sync -- you don't need to do anything with this list, it's")
        print("just here so you can sanity-check that the mapped fields above are select-type")
        print("where you'd expect, e.g. region/district):\n")
        seen = set()
        for path, (kind, list_name) in sorted(select_map.items()):
            if list_name in seen:
                continue
            seen.add(list_name)
            sample = list(choice_labels.get(list_name, {}).items())[:4]
            sample_str = ", ".join(f"{c!r}->{l!r}" for c, l in sample) or "(no choices found)"
            print(f"  {path:30s} [{kind:5s}] list '{list_name}': {sample_str}")
    else:
        print("\nNo select-type questions detected (or the form schema couldn't be read) "
              "-- values for this form are used as-is, no code-to-label resolution applied.")


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
            schema = fetch_asset_schema(form["asset_uid"])
            select_map, choice_labels = build_choice_resolver(schema)
            transformed = [
                transform(r, form["field_map"], form["activity_type"], form.get("training_type"),
                          select_map, choice_labels)
                for r in records
            ]
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
