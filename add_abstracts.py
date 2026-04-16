#!/usr/bin/env python3
"""
add_abstracts.py
----------------
Reads a .bib file, fetches missing abstracts from OpenAlex (primary) with
Semantic Scholar as a fallback, and writes an enriched .bib file.

Usage:
    python add_abstracts.py input.bib                  # writes input_with_abstracts.bib
    python add_abstracts.py input.bib -o output.bib    # custom output path
    python add_abstracts.py input.bib --overwrite      # overwrite existing abstracts
    python add_abstracts.py input.bib --cache-file my_cache.json  # custom cache path
    python add_abstracts.py input.bib --email you@example.com     # polite-pool priority
    python add_abstracts.py input.bib --source openalex           # force one source
    python add_abstracts.py input.bib --source s2                 # force Semantic Scholar

Requirements:
    pip install bibtexparser requests
"""

import argparse
import time
import sys
import json
import requests
import bibtexparser
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bparser import BibTexParser
from pathlib import Path


# ---------------------------------------------------------------------------
# API endpoints & config
# ---------------------------------------------------------------------------

OPENALEX_API     = "https://api.openalex.org/works"
S2_API           = "https://api.semanticscholar.org/graph/v1/paper"
S2_FIELDS        = "abstract,title,year,authors,externalIds"

# OpenAlex allows 10 req/sec in the polite pool (with email); 1 req/sec otherwise.
# S2 allows ~1 req/sec unauthenticated.
# OPENALEX_DELAY   = 0.15   # ~7 req/sec, safely under the polite-pool limit
OPENALEX_DELAY = 1.2
S2_DELAY         = 1.2

DEFAULT_CACHE_FILE = ".abstracts_cache.json"
SOURCES = ("openalex", "s2", "both")


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def load_cache(cache_path: str) -> dict:
    """Load the cache from disk, returning an empty dict if it doesn't exist."""
    p = Path(cache_path)
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                cache = json.load(f)
            print(f"Loaded {len(cache)} cached entries from {cache_path}")
            return cache
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read cache ({e}); starting fresh.", file=sys.stderr)
    return {}


def save_cache(cache: dict, cache_path: str) -> None:
    """Persist the cache to disk."""
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"Warning: could not write cache ({e}).", file=sys.stderr)


def cache_key(source: str, kind: str, value: str, year: str = "") -> str:
    clean = value.lower().replace("{", "").replace("}", "").replace("\\", "").strip()
    return f"{source}::{kind}::{clean}::{year}".rstrip("::")


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

def _openalex_abstract(data: dict) -> str:
    """
    OpenAlex stores abstracts as an inverted index {"word": [positions]}.
    Reconstruct the plain-text abstract from it.
    """
    inv = data.get("abstract_inverted_index")
    if not inv:
        return ""
    pos_word = {}
    for word, positions in inv.items():
        for pos in positions:
            pos_word[pos] = word
    return " ".join(pos_word[p] for p in sorted(pos_word))


def _openalex_normalize(data: dict) -> dict | None:
    """Return a normalised result dict or None if no abstract available."""
    abstract = _openalex_abstract(data)
    if not abstract:
        return None
    return {
        "abstract": abstract,
        "title": data.get("title", ""),
        "year": data.get("publication_year"),
        "_source": "openalex",
    }


def openalex_fetch_by_doi(doi: str, cache: dict, email: str = "") -> dict | None:
    key = cache_key("openalex", "doi", doi)
    if key in cache:
        print(f"      (cache hit: OpenAlex DOI)")
        return cache[key]

    params = {"filter": f"doi:{doi}"}
    if email:
        params["mailto"] = email

    try:
        resp = requests.get(OPENALEX_API, params=params, timeout=10)
        if resp.status_code == 200:
            for item in resp.json().get("results", []):
                normalized = _openalex_normalize(item)
                if normalized:
                    cache[key] = normalized
                    return normalized
    except requests.RequestException:
        pass

    cache[key] = None
    return None


def openalex_fetch_by_arxiv(arxiv_id: str, cache: dict, email: str = "") -> dict | None:
    key = cache_key("openalex", "arxiv", arxiv_id)
    if key in cache:
        print(f"      (cache hit: OpenAlex ArXiv)")
        return cache[key]

    params = {"filter": f"locations.landing_page_url:arxiv.org/abs/{arxiv_id}"}
    if email:
        params["mailto"] = email

    try:
        resp = requests.get(OPENALEX_API, params=params, timeout=10)
        if resp.status_code == 200:
            for item in resp.json().get("results", []):
                normalized = _openalex_normalize(item)
                if normalized:
                    cache[key] = normalized
                    return normalized
    except requests.RequestException:
        pass

    cache[key] = None
    return None


def openalex_fetch_by_title(title: str, year: str, cache: dict, email: str = "") -> dict | None:
    key = cache_key("openalex", "title", title, year)
    if key in cache:
        print(f"      (cache hit: OpenAlex title)")
        return cache[key]

    clean_title = title.replace("{", "").replace("}", "").replace("\\", "")
    params = {
        "search": clean_title,
        "select": "title,abstract_inverted_index,publication_year",
    }
    if year:
        params["filter"] = f"publication_year:{year}"
    if email:
        params["mailto"] = email

    try:
        resp = requests.get(OPENALEX_API, params=params, timeout=10)
        if resp.status_code == 200:
            for item in resp.json().get("results", []):
                normalized = _openalex_normalize(item)
                if normalized:
                    cache[key] = normalized
                    return normalized
    except requests.RequestException:
        pass

    cache[key] = None
    return None


def search_openalex(entry: dict, cache: dict, email: str = "") -> dict | None:
    """Try DOI → ArXiv → title search against OpenAlex."""
    doi = entry.get("doi", "").strip()
    if doi:
        result = openalex_fetch_by_doi(doi, cache, email)
        if result:
            return result

    eprint = entry.get("eprint", "").strip()
    if eprint and entry.get("archiveprefix", "").lower() == "arxiv":
        result = openalex_fetch_by_arxiv(eprint, cache, email)
        if result:
            return result

    title = entry.get("title", "").strip()
    if title:
        result = openalex_fetch_by_title(title, entry.get("year", ""), cache, email)
        if result:
            return result

    return None


# ---------------------------------------------------------------------------
# Semantic Scholar  (fallback)
# ---------------------------------------------------------------------------

def s2_fetch_by_id(paper_id: str, cache: dict) -> dict | None:
    key = cache_key("s2", "id", paper_id)
    if key in cache:
        print(f"      (cache hit: S2 {paper_id})")
        return cache[key]

    url = f"{S2_API}/{paper_id}"
    try:
        resp = requests.get(url, params={"fields": S2_FIELDS}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("abstract"):
                data["_source"] = "s2"
                cache[key] = data
                return data
    except requests.RequestException:
        pass

    cache[key] = None
    return None


def s2_fetch_by_title(title: str, year: str, cache: dict) -> dict | None:
    key = cache_key("s2", "title", title, year)
    if key in cache:
        print(f"      (cache hit: S2 title)")
        return cache[key]

    clean_title = title.replace("{", "").replace("}", "").replace("\\", "")
    try:
        resp = requests.get(
            f"{S2_API}/search",
            params={"query": clean_title, "fields": S2_FIELDS, "limit": 3},
            timeout=10,
        )
        if resp.status_code == 200:
            for paper in resp.json().get("data", []):
                if not paper.get("abstract"):
                    continue
                if year and paper.get("year") and str(paper["year"]) != str(year):
                    continue
                paper["_source"] = "s2"
                cache[key] = paper
                return paper
    except requests.RequestException:
        pass

    cache[key] = None
    return None


def search_s2(entry: dict, cache: dict) -> dict | None:
    """Try DOI → ArXiv → title search against Semantic Scholar."""
    doi = entry.get("doi", "").strip()
    if doi:
        result = s2_fetch_by_id(f"DOI:{doi}", cache)
        if result:
            return result

    eprint = entry.get("eprint", "").strip()
    if eprint and entry.get("archiveprefix", "").lower() == "arxiv":
        result = s2_fetch_by_id(f"ARXIV:{eprint}", cache)
        if result:
            return result

    title = entry.get("title", "").strip()
    if title:
        result = s2_fetch_by_title(title, entry.get("year", ""), cache)
        if result:
            return result

    return None


# ---------------------------------------------------------------------------
# Unified search  (OpenAlex → S2 fallback)
# ---------------------------------------------------------------------------

def search_for_abstract(
    entry: dict,
    cache: dict,
    source: str = "both",
    email: str = "",
) -> tuple[dict | None, bool]:
    """
    Search for an abstract using the configured source(s).
    Returns (result, was_cache_hit).
    """
    cache_size_before = sum(1 for v in cache.values() if v is not None)

    result = None
    if source in ("openalex", "both"):
        result = search_openalex(entry, cache, email)
    if result is None and source in ("s2", "both"):
        result = search_s2(entry, cache)

    cache_size_after = sum(1 for v in cache.values() if v is not None)
    was_cache_hit = cache_size_after == cache_size_before

    return result, was_cache_hit


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_bib_file(
    input_path: str,
    output_path: str,
    overwrite: bool = False,
    cache_path: str = DEFAULT_CACHE_FILE,
    source: str = "both",
    email: str = "",
) -> None:
    input_file = Path(input_path)
    if not input_file.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    cache = load_cache(cache_path)

    source_label = {
        "openalex": "OpenAlex only",
        "s2": "Semantic Scholar only",
        "both": "OpenAlex → S2 fallback",
    }[source]
    print(f"Source strategy : {source_label}")
    if email:
        print(f"Polite-pool email: {email}")
    print()

    print(f"Reading {input_path}...")
    with open(input_path, encoding="utf-8") as f:
        parser = BibTexParser(common_strings=True)
        parser.ignore_nonstandard_types = False
        bib_db = bibtexparser.load(f, parser)

    entries = bib_db.entries
    print(f"Found {len(entries)} entries.\n")

    found = skipped = not_found = cache_hits = 0
    source_counts: dict[str, int] = {}

    for i, entry in enumerate(entries):
        entry_key = entry.get("ID", f"entry_{i}")
        title = entry.get("title", "(no title)").replace("{", "").replace("}", "")[:70]

        if entry.get("abstract") and not overwrite:
            print(f"  [{i+1}/{len(entries)}] SKIP (has abstract): {entry_key}")
            skipped += 1
            continue

        print(f"  [{i+1}/{len(entries)}] Searching: {entry_key} — {title}...")

        result, was_cache_hit = search_for_abstract(entry, cache, source=source, email=email)

        if result and result.get("abstract"):
            abstract = result["abstract"].strip()
            entry["abstract"] = abstract
            src = result.get("_source", "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1

            if was_cache_hit:
                print(f"    ✓ [{src}] Abstract from cache ({len(abstract)} chars)")
                cache_hits += 1
            else:
                print(f"    ✓ [{src}] Abstract fetched ({len(abstract)} chars)")
            found += 1
        else:
            print(f"    ✗ Not found")
            not_found += 1

        # Respect rate limits only on live requests; skip sleep for cache hits.
        # Use the more conservative S2 delay when S2 may have been queried.
        if not was_cache_hit:
            delay = S2_DELAY if source in ("s2", "both") else OPENALEX_DELAY
            time.sleep(delay)

    save_cache(cache, cache_path)

    writer = BibTexWriter()
    writer.indent = "  "
    writer.comma_first = False
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(writer.write(bib_db))

    print(f"\nDone!")
    print(f"  ✓ Abstracts added : {found}  (of which {cache_hits} from cache)")
    for src, count in source_counts.items():
        print(f"      via {src:<12}: {count}")
    print(f"  - Already had one : {skipped}")
    print(f"  ✗ Not found       : {not_found}")
    print(f"  💾 Cache saved to : {cache_path}")
    print(f"\nOutput written to: {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Add abstracts to a .bib file using OpenAlex (+ optional S2 fallback)."
    )
    parser.add_argument("input", help="Input .bib file")
    parser.add_argument("-o", "--output", help="Output .bib file (default: input_with_abstracts.bib)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite abstracts that already exist")
    parser.add_argument(
        "--cache-file",
        default=DEFAULT_CACHE_FILE,
        help=f"Path to the local JSON cache file (default: {DEFAULT_CACHE_FILE})",
    )
    parser.add_argument(
        "--source",
        choices=SOURCES,
        default="both",
        help=(
            "Which API(s) to use: 'openalex' (primary, generous limits), "
            "'s2' (Semantic Scholar), or 'both' (OpenAlex first, S2 as fallback). "
            "Default: both"
        ),
    )
    parser.add_argument(
        "--email",
        default="",
        help="Your email for the OpenAlex polite pool (10 req/sec vs 1 req/sec — recommended)",
    )
    args = parser.parse_args()

    output_path = args.output or str(
        Path(args.input).with_stem(Path(args.input).stem + "_with_abstracts")
    )
    process_bib_file(
        args.input,
        output_path,
        overwrite=args.overwrite,
        cache_path=args.cache_file,
        source=args.source,
        email=args.email,
    )


if __name__ == "__main__":
    main()
