#!/usr/bin/env python3
"""
add_abstracts.py
----------------
Reads a .bib file, fetches missing abstracts from Semantic Scholar,
and writes an enriched .bib file.

Usage:
    python add_abstracts.py input.bib                  # writes input_with_abstracts.bib
    python add_abstracts.py input.bib -o output.bib    # custom output path
    python add_abstracts.py input.bib --overwrite      # overwrite existing abstracts

Requirements:
    pip install bibtexparser requests
"""

import argparse
import time
import sys
import requests
import bibtexparser
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bparser import BibTexParser
from pathlib import Path


SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper"
FIELDS = "abstract,title,year,authors,externalIds"
RATE_LIMIT_DELAY = 1.2  # seconds between requests (S2 allows ~1 req/sec unauthenticated)


def search_semantic_scholar(entry: dict) -> dict | None:
    """Try multiple lookup strategies to find a paper on Semantic Scholar."""

    # Strategy 1: DOI lookup (most reliable)
    doi = entry.get("doi", "").strip()
    if doi:
        result = fetch_by_id(f"DOI:{doi}")
        if result:
            return result

    # Strategy 2: ArXiv ID
    eprint = entry.get("eprint", "").strip()
    archiveprefix = entry.get("archiveprefix", "").lower()
    if eprint and archiveprefix == "arxiv":
        result = fetch_by_id(f"ARXIV:{eprint}")
        if result:
            return result

    # Strategy 3: Title + year search
    title = entry.get("title", "").strip()
    if title:
        result = fetch_by_title_search(title, entry.get("year", ""))
        if result:
            return result

    return None


def fetch_by_id(paper_id: str) -> dict | None:
    """Fetch paper details using a known ID (DOI, ArXiv, etc.)."""
    url = f"{SEMANTIC_SCHOLAR_API}/{paper_id}"
    try:
        resp = requests.get(url, params={"fields": FIELDS}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("abstract"):
                return data
    except requests.RequestException:
        pass
    return None


def fetch_by_title_search(title: str, year: str = "") -> dict | None:
    """Search by title and return the best match."""
    # Clean LaTeX formatting from title
    clean_title = title.replace("{", "").replace("}", "").replace("\\", "")

    url = f"{SEMANTIC_SCHOLAR_API}/search"
    params = {"query": clean_title, "fields": FIELDS, "limit": 3}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("data", [])
            for paper in results:
                if not paper.get("abstract"):
                    continue
                # Optionally check year matches
                if year and paper.get("year"):
                    if str(paper["year"]) != str(year):
                        continue
                return paper
    except requests.RequestException:
        pass
    return None


def process_bib_file(input_path: str, output_path: str, overwrite: bool = False) -> None:
    """Main processing function."""
    input_file = Path(input_path)
    if not input_file.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {input_path}...")
    with open(input_path, encoding="utf-8") as f:
        parser = BibTexParser(common_strings=True)
        parser.ignore_nonstandard_types = False
        bib_db = bibtexparser.load(f, parser)

    entries = bib_db.entries
    print(f"Found {len(entries)} entries.\n")

    found = 0
    skipped = 0
    not_found = 0

    for i, entry in enumerate(entries):
        entry_key = entry.get("ID", f"entry_{i}")
        entry_type = entry.get("ENTRYTYPE", "?")
        title = entry.get("title", "(no title)").replace("{", "").replace("}", "")[:70]

        # Skip if abstract already present and not overwriting
        if entry.get("abstract") and not overwrite:
            print(f"  [{i+1}/{len(entries)}] SKIP (has abstract): {entry_key}")
            skipped += 1
            continue

        print(f"  [{i+1}/{len(entries)}] Searching: {entry_key} — {title}...")

        result = search_semantic_scholar(entry)

        if result and result.get("abstract"):
            abstract = result["abstract"].strip()
            entry["abstract"] = abstract
            print(f"    ✓ Abstract found ({len(abstract)} chars)")
            found += 1
        else:
            print(f"    ✗ Not found")
            not_found += 1

        time.sleep(RATE_LIMIT_DELAY)

    # Write output
    writer = BibTexWriter()
    writer.indent = "  "
    writer.comma_first = False

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(writer.write(bib_db))

    print(f"\nDone!")
    print(f"  ✓ Abstracts added:    {found}")
    print(f"  - Already had one:    {skipped}")
    print(f"  ✗ Not found:          {not_found}")
    print(f"\nOutput written to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Add abstracts to a .bib file using Semantic Scholar."
    )
    parser.add_argument("input", help="Input .bib file")
    parser.add_argument("-o", "--output", help="Output .bib file (default: input_with_abstracts.bib)")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite abstracts that already exist in the file",
    )
    args = parser.parse_args()

    if args.output:
        output_path = args.output
    else:
        p = Path(args.input)
        output_path = str(p.with_stem(p.stem + "_with_abstracts"))

    process_bib_file(args.input, output_path, overwrite=args.overwrite)


if __name__ == "__main__":
    main()