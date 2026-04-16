#!/usr/bin/env python3
"""
dedup_bib.py
------------
Removes duplicate entries in a .bib file based on cite key.
For each duplicated key, the entry with the most fields is kept.

Usage:
    python dedup_bib.py refs.bib                  # writes refs_deduped.bib
    python dedup_bib.py refs.bib -o clean.bib     # custom output
    python dedup_bib.py refs.bib --dry-run        # report only, no file written
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter


def entry_fullness(entry: dict) -> int:
    """Count non-empty fields (excluding internal keys) — used to pick the best copy."""
    skip = {"ID", "ENTRYTYPE"}
    return sum(1 for k, v in entry.items() if k not in skip and str(v).strip())


def main():
    parser = argparse.ArgumentParser(
        description="Deduplicate .bib entries by cite key."
    )
    parser.add_argument("bib", help="Input .bib file")
    parser.add_argument("-o", "--output", help="Output path (default: <input>_deduped.bib)")
    parser.add_argument("--dry-run", action="store_true", help="Report duplicates without writing output")
    args = parser.parse_args()

    bib_path = Path(args.bib)
    if not bib_path.exists():
        print(f"Error: {args.bib} not found.", file=sys.stderr)
        sys.exit(1)

    with open(bib_path, encoding="utf-8", errors="replace") as f:
        bib_parser = BibTexParser(common_strings=True)
        bib_parser.ignore_nonstandard_types = False
        bib_db = bibtexparser.load(f, bib_parser)

    # Group entries by cite key
    groups = defaultdict(list)
    for entry in bib_db.entries:
        groups[entry["ID"]].append(entry)

    duplicates = {key: entries for key, entries in groups.items() if len(entries) > 1}

    # ── Report ────────────────────────────────────────────────────────────────
    total_extra_copies = sum(len(v) - 1 for v in duplicates.values())

    print(f"\n  Total entries            : {len(bib_db.entries)}")
    print(f"  Unique keys              : {len(groups)}")
    print(f"  Duplicated keys          : {len(duplicates)}")
    print(f"  Extra copies to remove   : {total_extra_copies}\n")

    if duplicates:
        print("DUPLICATES FOUND:\n")
        for key, entries in sorted(duplicates.items()):
            print(f"  {key}  ({len(entries)} copies)")
            for e in entries:
                title = e.get("title", "").replace("{", "").replace("}", "")[:60]
                print(f"    [{entry_fullness(e):2d} fields]  {title}")
        print()
    else:
        print("No duplicate keys found. ✓\n")
        return

    if args.dry_run:
        print("Dry run — no file written.\n")
        return

    # ── Keep the fullest copy of each key ─────────────────────────────────────
    # Preserve the order of first appearance
    seen = {}
    for entry in bib_db.entries:
        key = entry["ID"]
        if key not in seen or entry_fullness(entry) > entry_fullness(seen[key]):
            seen[key] = entry

    bib_db.entries = list(seen.values())

    out_path = args.output or str(bib_path.with_stem(bib_path.stem + "_deduped"))

    writer = BibTexWriter()
    writer.indent = "  "
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(writer.write(bib_db))

    print(f"Cleaned .bib written to: {out_path}")
    print(f"  Kept    : {len(bib_db.entries)} entries")
    print(f"  Removed : {total_extra_copies} duplicate copies\n")


if __name__ == "__main__":
    main()