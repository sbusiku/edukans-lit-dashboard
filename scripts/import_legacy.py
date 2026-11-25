#!/usr/bin/env python3
"""
One-time import of the old training baseline (scripts/baseline_training.json) into Firebase.
Run it once from the 'Import legacy data' GitHub Action, then DELETE baseline_training.json
from the repository so the data no longer sits in public.
"""
import json
import os
import sys

import firestore_sync

HERE = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(HERE, "baseline_training.json")
if not os.path.exists(path):
    print("baseline_training.json not found: nothing to import.", file=sys.stderr)
    sys.exit(1)
records = json.load(open(path, encoding="utf-8"))
for r in records:
    r["activity_type"] = "Training"
    r["training_type"] = "Legacy"
firestore_sync.publish(legacy=records)
