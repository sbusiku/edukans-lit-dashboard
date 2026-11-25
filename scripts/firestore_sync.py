#!/usr/bin/env python3
"""
firestore_sync.py
-----------------
Publishes the dashboard's data to Firebase (Firestore) so it is NOT stored in the public
GitHub repo any more. Uses only the Python standard library and NO downloaded key file:
it signs in as a dedicated "sync" login (a normal Firebase email/password user) and writes
through Firestore's REST API. The Firestore rules only let that one login write data.

Collections it writes
  datasets/{source}__{region}__{tool}__{part}   full records, grouped by region so the rules
                                                can show an Officer only their own region.
                                                source = "live" (from KoBo) or "legacy"
                                                (the old baseline, imported once).
  public_datasets/{tool}__{part}                anonymised copies for Guest/Partner users:
                                                no names, venues, notes or GPS; schools are
                                                replaced by an opaque id; dates are month-only.
  meta/sync                                     when the last sync happened.

Environment (set as GitHub secrets):
  FIREBASE_API_KEY, FIREBASE_PROJECT_ID, FIREBASE_SYNC_EMAIL, FIREBASE_SYNC_PASSWORD
"""
import base64
import datetime
import gzip
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

CHUNK = 1000         # records per full-detail document (stored gzip-compressed, so ~100 KB each)
PUBLIC_CHUNK = 2000
FORMAT_VERSION = 2   # bump if the stored layout ever changes: forces one full rewrite
TOOL_KEY = {"Training": "training", "Radio Talkshow": "radio",
            "Community Dialogue": "dialogue", "Back to School": "backtoschool"}


# ------------------------------------------------------------------ REST helpers
def _env(name):
    v = os.environ.get(name, "").strip()
    if not v:
        print(f"ERROR: environment variable {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return v


def _call(method, url, body=None, token=None, allow_404=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 404 and allow_404:
            return None
        print(f"ERROR: {method} {url.split('?')[0]} -> HTTP {e.code}", file=sys.stderr)
        print(e.read().decode("utf-8", errors="ignore")[:800], file=sys.stderr)
        sys.exit(1)


def sign_in():
    url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + _env("FIREBASE_API_KEY")
    out = _call("POST", url, {"email": _env("FIREBASE_SYNC_EMAIL"),
                              "password": _env("FIREBASE_SYNC_PASSWORD"), "returnSecureToken": True})
    return out["idToken"]


def _docs_base():
    return f"projects/{_env('FIREBASE_PROJECT_ID')}/databases/(default)/documents"


def enc(v):
    """Python value -> Firestore REST value."""
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: enc(x) for k, x in v.items()}}}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [enc(x) for x in v]}}
    raise TypeError(f"cannot encode {type(v)}")


def dec(v):
    """Firestore REST value -> Python value."""
    if "nullValue" in v:
        return None
    if "booleanValue" in v:
        return v["booleanValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "stringValue" in v:
        return v["stringValue"]
    if "timestampValue" in v:
        return v["timestampValue"]
    if "mapValue" in v:
        return {k: dec(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [dec(x) for x in v["arrayValue"].get("values", [])]
    return None


def list_docs(collection, token):
    """Returns {doc_id: python_dict} for every document in a collection."""
    out, page = {}, None
    while True:
        url = f"https://firestore.googleapis.com/v1/{_docs_base()}/{collection}?pageSize=200"
        if page:
            url += "&pageToken=" + page
        res = _call("GET", url, token=token)
        for d in res.get("documents", []):
            out[d["name"].rsplit("/", 1)[1]] = {k: dec(x) for k, x in d.get("fields", {}).items()}
        page = res.get("nextPageToken")
        if not page:
            return out


def get_doc(collection, doc_id, token):
    res = _call("GET", f"https://firestore.googleapis.com/v1/{_docs_base()}/{collection}/{doc_id}", token=token, allow_404=True)
    if not res:
        return None
    return {k: dec(x) for k, x in res.get("fields", {}).items()}


def commit(writes, token):
    url = f"https://firestore.googleapis.com/v1/{_docs_base()}:commit"
    for i in range(0, len(writes), 100):
        _call("POST", url, {"writes": writes[i:i + 100]}, token)


def _update(collection, doc_id, fields):
    return {"update": {"name": f"{_docs_base()}/{collection}/{doc_id}",
                       "fields": {k: enc(v) for k, v in fields.items()}}}


def _delete(collection, doc_id):
    return {"delete": f"{_docs_base()}/{collection}/{doc_id}"}


# ------------------------------------------------------------------ data shaping
def title_case(s):
    return re.sub(r"(^|[\s\-/])([a-z])", lambda m: m.group(1) + m.group(2).upper(), str(s or "").strip().lower())


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "unknown"


def tool_of(r):
    return TOOL_KEY.get(r.get("activity_type"), "other")


def region_of(r):
    reg = r.get("region")
    return title_case(reg) if reg and reg != "Unknown" else "Unknown"


_SCHOOL_WORDS = re.compile(r"(?:^|[^a-z])(?:school|sch|sec|secondary|primary|college|high|academy|seed|vocational|institute|s\.?s\.?s?|p\.?s)(?![a-z])", re.I)
_NOT_SCHOOL = re.compile(r"health|hospital|clinic", re.I)


def _norm_school(s):
    s = re.sub(r"[^a-z0-9]+", " ", str(s or "").lower())
    s = re.sub(r"\bthe\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def school_key(r):
    """Same rule the dashboard uses to decide which school (if any) a record reached."""
    if str(r.get("school") or "").strip():
        return _norm_school(r["school"]) or None
    v = r.get("venue") or ""
    if v and _SCHOOL_WORDS.search(v) and not _NOT_SCHOOL.search(v):
        return _norm_school(v) or None
    return None


def sanitize(r):
    """Anonymised copy for Guest/Partner users. Keeps only what the Overview and Indicators need."""
    out = {
        "activity_type": r.get("activity_type"),
        "region": region_of(r),
        "district": r.get("district"),
        "date": (str(r.get("date") or "")[:7] + "-01") if r.get("date") else "",   # month only
        "male": r.get("male", 0), "female": r.get("female", 0),
        "pwd_male": r.get("pwd_male", 0), "pwd_female": r.get("pwd_female", 0),
        "total": r.get("total", 0),
    }
    if r.get("training_type"):
        out["training_type"] = r["training_type"]
    if r.get("participant_category"):
        out["participant_category"] = r["participant_category"]
    if r.get("category_counts"):
        out["category_counts"] = r["category_counts"]
    k = school_key(r)
    if k:
        out["school"] = "s" + hashlib.sha1(k.encode("utf-8")).hexdigest()[:10]   # opaque id, same school -> same id
    return out


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def pack(items):
    """records -> (compressed base64 text, hash of the uncompressed content).
    The hash lets later syncs skip documents whose content has not changed."""
    raw = json.dumps(items, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    blob = base64.b64encode(gzip.compress(raw, 9, mtime=0)).decode("ascii")
    return blob, hashlib.sha256(raw).hexdigest()


def unpack(fields):
    """Reads a stored document back into a list of records."""
    if "blob" in fields:
        return json.loads(gzip.decompress(base64.b64decode(fields["blob"])).decode("utf-8"))
    return fields.get("items", [])


def _order(r):
    return (str(r.get("date") or ""), str(r.get("submitted") or ""), str(r.get("district") or ""), str(r.get("venue") or ""))


def detail_docs(records, source):
    """{doc_id: fields} for full-detail records, one group per region + tool (stable order, so only
    the newest chunk of a group changes when records are added)."""
    groups = {}
    for r in records:
        r = dict(r)
        r["region"] = region_of(r)
        groups.setdefault((r["region"], tool_of(r)), []).append(r)
    docs = {}
    for (region, tool), rows in groups.items():
        rows.sort(key=_order)
        for part, rows_part in enumerate(_chunks(rows, CHUNK)):
            blob, h = pack(rows_part)
            docs[f"{source}__{slug(region)}__{tool}__{part:03d}"] = {
                "source": source, "region": region, "tool": tool, "part": part,
                "count": len(rows_part), "hash": h, "blob": blob}
    return docs


def public_docs(records):
    groups = {}
    for r in records:
        groups.setdefault(tool_of(r), []).append(sanitize(r))
    docs = {}
    for tool, rows in groups.items():
        rows.sort(key=lambda x: (x.get("date") or "", x.get("region") or "", str(x.get("district") or "")))
        for part, rows_part in enumerate(_chunks(rows, PUBLIC_CHUNK)):
            blob, h = pack(rows_part)
            docs[f"{tool}__{part:03d}"] = {"tool": tool, "part": part, "count": len(rows_part), "hash": h, "blob": blob}
    return docs


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _fingerprint(rows):
    raw = json.dumps(rows, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest() + f":v{FORMAT_VERSION}"


# ------------------------------------------------------------------ publish
def publish(live=None, legacy=None):
    """
    Writes whichever of `live` / `legacy` is given, reads the other back from Firebase, then rebuilds the
    anonymised public copy from both. To stay far inside Firebase's free limits it:
      - exits after a single read when a live sync finds nothing has changed, and
      - writes only the documents whose content actually changed.
    """
    token = sign_in()
    meta = get_doc("meta", "sync", token) or {}

    if live is not None and legacy is None and meta.get("live_hash") == _fingerprint(live):
        print(f"No change since the last sync ({len(live)} live records): nothing written.")
        return

    existing = list_docs("datasets", token)

    def items_of(source):
        rows = []
        for doc_id, f in existing.items():
            if doc_id.startswith(source + "__"):
                rows.extend(unpack(f))
        return rows

    live_rows = live if live is not None else items_of("live")
    legacy_rows = legacy if legacy is not None else items_of("legacy")

    writes, changed, skipped = [], 0, 0

    def plan(collection, new_docs, old_docs, prefix=None):
        nonlocal changed, skipped
        for doc_id, fields in new_docs.items():
            if old_docs.get(doc_id, {}).get("hash") == fields["hash"]:
                skipped += 1
                continue
            fields = dict(fields, updated=_now())
            writes.append(_update(collection, doc_id, fields)); changed += 1
        for doc_id in old_docs:
            if (prefix is None or doc_id.startswith(prefix)) and doc_id not in new_docs:
                writes.append(_delete(collection, doc_id)); changed += 1

    for source, rows, given in (("live", live_rows, live is not None), ("legacy", legacy_rows, legacy is not None)):
        if given:
            plan("datasets", detail_docs(rows, source), {k: v for k, v in existing.items() if k.startswith(source + "__")}, source + "__")

    pub_new = public_docs(legacy_rows + live_rows)
    plan("public_datasets", pub_new, list_docs("public_datasets", token))

    counts = {}
    for r in legacy_rows + live_rows:
        counts[tool_of(r)] = counts.get(tool_of(r), 0) + 1
    new_meta = {"updated": _now(), "records": len(live_rows) + len(legacy_rows), "counts": counts,
                "live_hash": _fingerprint(live_rows) if live is not None else meta.get("live_hash", ""),
                "legacy_count": len(legacy_rows)}
    writes.append(_update("meta", "sync", new_meta))

    commit(writes, token)
    print(f"Published {len(live_rows)} live + {len(legacy_rows)} legacy records: "
          f"{changed} documents written, {skipped} unchanged and skipped.")
