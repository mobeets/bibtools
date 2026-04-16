#!/usr/bin/env python3
"""
find_uncited.py
---------------
Compares a .bib file against a .tex file (or a directory of .tex files)
and reports which bib entries are never cited in the tex source.

Usage:
    python find_uncited.py paper.tex refs.bib
    python find_uncited.py paper.tex refs.bib --delete   # write a cleaned .bib without uncited entries
    python find_uncited.py tex_dir/  refs.bib            # scan all .tex files in a directory
"""

import argparse
import re
import sys
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter


# Matches \cite{key}, \citep{key}, \citet{key,key2}, \citealp{...}, etc.
# Also handles optional args like \cite[p.~5]{key}
CITE_PATTERN = re.compile(
    r"\\cite[a-zA-Z*]*\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}"
)


def extract_cited_keys(tex_source: str) -> set[str]:
    """Return the set of all citation keys used in tex source text."""
    keys = set()
    for match in CITE_PATTERN.finditer(tex_source):
        for key in match.group(1).split(","):
            keys.add(key.strip())
    return keys


def load_tex_source(tex_path: str) -> str:
    """Load .tex source — if a directory is given, concatenate all .tex files in it."""
    p = Path(tex_path)
    if p.is_dir():
        sources = []
        files = sorted(p.rglob("*.tex"))
        if not files:
            print(f"Error: no .tex files found in {tex_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Found {len(files)} .tex file(s) in {tex_path}:")
        for f in files:
            print(f"  {f}")
            sources.append(f.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(sources)
    elif p.is_file():
        return p.read_text(encoding="utf-8", errors="replace")
    else:
        print(f"Error: {tex_path} is not a file or directory.", file=sys.stderr)
        sys.exit(1)


def load_bib(bib_path: str):
    """Parse and return a BibDatabase."""
    p = Path(bib_path)
    if not p.exists():
        print(f"Error: {bib_path} not found.", file=sys.stderr)
        sys.exit(1)
    with open(bib_path, encoding="utf-8", errors="replace") as f:
        parser = BibTexParser(common_strings=True)
        parser.ignore_nonstandard_types = False
        return bibtexparser.load(f, parser)


def format_entry_summary(entry: dict) -> str:
    """One-line human-readable summary of a bib entry."""
    key = entry.get("ID", "?")
    authors = entry.get("author", "")
    # Take only first author's last name
    first_author = authors.split(" and ")[0].strip()
    if "," in first_author:
        first_author = first_author.split(",")[0].strip()
    else:
        first_author = first_author.split()[-1] if first_author.split() else ""
    year = entry.get("year", "")
    title = entry.get("title", "").replace("{", "").replace("}", "")[:55]
    return f"{key:<30} {first_author} {year}  {title}"


def main():
    parser = argparse.ArgumentParser(
        description="Find .bib entries not cited in a .tex file."
    )
    parser.add_argument("tex", help=".tex file or directory of .tex files")
    parser.add_argument("bib", help=".bib file")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Write a new .bib file with uncited entries removed",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output path for cleaned .bib (default: <input>_cited_only.bib)",
    )
    args = parser.parse_args()

    # Load inputs
    tex_source = load_tex_source(args.tex)
    bib_db = load_bib(args.bib)

    cited_keys = extract_cited_keys(tex_source)
    bib_keys = {entry["ID"] for entry in bib_db.entries}

    uncited = bib_keys - cited_keys
    phantom = cited_keys - bib_keys  # cited in tex but missing from bib

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  .tex citations found : {len(cited_keys)}")
    print(f"  .bib entries total   : {len(bib_keys)}")
    print(f"{'─'*60}\n")

    if uncited:
        print(f"IN .BIB BUT NOT CITED IN .TEX  ({len(uncited)} entries)\n")
        uncited_entries = [e for e in bib_db.entries if e["ID"] in uncited]
        # Sort by key for readability
        for entry in sorted(uncited_entries, key=lambda e: e["ID"].lower()):
            print(f"  {format_entry_summary(entry)}")
    else:
        print("All .bib entries are cited in the .tex file. ✓")

    if phantom:
        print(f"\nCITED IN .TEX BUT MISSING FROM .BIB  ({len(phantom)} keys)\n")
        for key in sorted(phantom):
            print(f"  {key}")

    print()

    # ── Optionally write cleaned .bib ─────────────────────────────────────────
    if args.delete:
        bib_db.entries = [e for e in bib_db.entries if e["ID"] in cited_keys]

        if args.output:
            out_path = args.output
        else:
            p = Path(args.bib)
            out_path = str(p.with_stem(p.stem + "_cited_only"))

        writer = BibTexWriter()
        writer.indent = "  "
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(writer.write(bib_db))

        print(f"Cleaned .bib written to: {out_path}")
        print(f"  Kept    : {len(bib_db.entries)} entries")
        print(f"  Removed : {len(uncited)} entries\n")


if __name__ == "__main__":
    main()