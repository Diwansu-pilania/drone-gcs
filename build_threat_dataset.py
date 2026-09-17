"""Build a threat-scoring dataset from stored detections.

Reads the merged detection records the GCS writes and emits one row per
detection, with the features a model would use, the computed threat score and
its components, and an empty ``threat_label`` column.

    python build_threat_dataset.py
    python build_threat_dataset.py --out data/threat.csv --jsonl data/threat.jsonl
    python build_threat_dataset.py --require-checkpoints --min-confidence 0.5

Sources, both read by default:
  detection_records/<image_file>.json   one file per detection, overwritten in
                                        place, so always the latest state.
  detections.jsonl                      append-only, MANY lines per detection
                                        as it progresses (POST, projection,
                                        checkpoints). Only the last line for
                                        each detection is kept; taking every
                                        line would fill the dataset with
                                        half-finished duplicates of the same
                                        object and bias whatever trains on it.

READ THIS ABOUT ``threat_label``
--------------------------------
``threat_severity`` is computed by a formula in core/threat_scoring.py. A model
trained on it as the target learns only to reproduce that formula, which is
already free and exact — it adds nothing and hides the formula's own mistakes
behind a model's confidence. It is written out as a BASELINE and as features,
not as a target.

For supervised training, ``threat_label`` needs values that do not come from
the formula: an operator's judgement, or an after-the-fact outcome. Once some
rows are labelled, the computed severity becomes a useful thing to compare
against — if a model cannot beat it, the formula is enough.
"""

import argparse
import csv
import glob
import json
import os
import sys
from collections import Counter

from core.threat_scoring import (FEATURE_COLUMNS, SCORING_VERSION,
                                 detection_identity, extract_features,
                                 score_detection)

DEFAULT_RECORD_DIR = "detection_records"
DEFAULT_LOG_FILE = "detections.jsonl"
DEFAULT_OUT_CSV = "threat_dataset.csv"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records", default=DEFAULT_RECORD_DIR,
                        help=f"per-detection JSON directory "
                             f"(default: {DEFAULT_RECORD_DIR})")
    parser.add_argument("--log", default=DEFAULT_LOG_FILE,
                        help=f"append-only event log (default: {DEFAULT_LOG_FILE})")
    parser.add_argument("--out", default=DEFAULT_OUT_CSV,
                        help=f"CSV to write (default: {DEFAULT_OUT_CSV})")
    parser.add_argument("--jsonl", default=None,
                        help="also write rows as JSON Lines to this path")
    parser.add_argument("--require-checkpoints", action="store_true",
                        help="keep only detections whose /nearby lookup "
                             "succeeded, so exposure is a real 0 and not an "
                             "unknown one")
    parser.add_argument("--require-position", action="store_true",
                        help="keep only detections positioned from real "
                             "telemetry, dropping default-centre estimates")
    parser.add_argument("--min-confidence", type=float, default=None,
                        help="drop detections below this detector confidence")
    return parser.parse_args(argv)


def load_records(record_dir, log_file):
    """Return the latest state of every detection found, plus a source tally.

    A record file wins over the log, being the record store's own current
    state; within the log, the last line for a detection wins.
    """
    latest = {}
    counts = Counter()

    for path in sorted(glob.glob(os.path.join(record_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"  skipped {path}: {exc}", file=sys.stderr)
            counts["unreadable"] += 1
            continue
        if not isinstance(record, dict):
            counts["unreadable"] += 1
            continue
        key = detection_identity(record) or path
        latest[key] = record
        counts["from_records"] += 1

    from_records = set(latest)
    if os.path.exists(log_file):
        with open(log_file, encoding="utf-8") as f:
            for number, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError as exc:
                    print(f"  skipped {log_file}:{number}: {exc}",
                          file=sys.stderr)
                    counts["unreadable"] += 1
                    continue
                if not isinstance(record, dict):
                    counts["unreadable"] += 1
                    continue
                key = detection_identity(record)
                if key is None:
                    counts["no_identity"] += 1
                    continue
                if key in from_records:
                    counts["log_superseded_by_record"] += 1
                    continue
                # Later lines are progressively more complete, so overwrite.
                if key in latest:
                    counts["log_line_superseded"] += 1
                latest[key] = record

    counts["from_log"] = len(latest) - len(from_records)
    return latest, counts


def keep(row, args):
    """Whether a row survives the requested filters, and why not."""
    if args.require_checkpoints and row["checkpoint_lookup_status"] != "ok":
        return False, "no successful checkpoint lookup"
    if args.require_position and row["position_is_estimated"]:
        return False, "position estimated from the default centre"
    if args.min_confidence is not None:
        confidence = row["confidence"]
        if confidence is None or confidence < args.min_confidence:
            return False, f"confidence below {args.min_confidence}"
    return True, None


def main(argv=None):
    args = parse_args(argv)

    print(f"scoring version {SCORING_VERSION}")
    print(f"reading {args.records}/*.json and {args.log}")
    records, counts = load_records(args.records, args.log)
    print(f"  {counts['from_records']} record file(s), "
          f"{counts['from_log']} more from the log")
    if counts["log_line_superseded"]:
        print(f"  {counts['log_line_superseded']} earlier log line(s) "
              f"superseded by a later state of the same detection")
    if counts["log_superseded_by_record"]:
        print(f"  {counts['log_superseded_by_record']} log line(s) ignored in "
              f"favour of a record file")
    if counts["unreadable"]:
        print(f"  {counts['unreadable']} unreadable entr(y/ies) skipped")

    if not records:
        print("\nNo detections found. Run the GCS so it writes "
              f"{args.records}/ and {args.log} first.")
        return 1

    rows, dropped = [], Counter()
    for key in sorted(records):
        record = records[key]
        try:
            row = extract_features(record, score_detection(record))
        except Exception as exc:            # one bad record must not stop the run
            print(f"  could not score {key}: {exc}", file=sys.stderr)
            dropped["could not score"] += 1
            continue
        wanted, why = keep(row, args)
        if not wanted:
            dropped[why] += 1
            continue
        rows.append(row)

    for why, count in dropped.items():
        print(f"  dropped {count}: {why}")

    if not rows:
        print("\nEvery detection was filtered out; loosen the filters.")
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FEATURE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {len(rows)} row(s) x {len(FEATURE_COLUMNS)} column(s) "
          f"to {args.out}")

    if args.jsonl:
        os.makedirs(os.path.dirname(os.path.abspath(args.jsonl)) or ".",
                    exist_ok=True)
        with open(args.jsonl, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"wrote {len(rows)} row(s) to {args.jsonl}")

    # A quick shape report: a dataset this skewed is worth knowing about
    # before training rather than after.
    bands = Counter(row["threat_band"] for row in rows)
    classes = Counter(row["object_class"] for row in rows)
    with_cp = sum(1 for row in rows if row["checkpoint_lookup_status"] == "ok")
    in_range = sum(1 for row in rows if row["any_checkpoint_in_range"])
    estimated = sum(1 for row in rows if row["position_is_estimated"])

    print("\n--- dataset shape ---")
    print(f"  object classes      {dict(classes)}")
    print(f"  baseline bands      {dict(bands)}")
    print(f"  checkpoint lookup   {with_cp}/{len(rows)} succeeded, "
          f"{in_range} had one in range")
    print(f"  position estimated  {estimated}/{len(rows)} from the default centre")
    print(f"  labelled            0/{len(rows)}  <- threat_label is empty")
    print("\nthreat_severity is a formula's output, not a target: training on "
          "it only\nreproduces the formula. Fill threat_label with an "
          "operator judgement or a\nrecorded outcome, then use the severity "
          "as the baseline to beat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
