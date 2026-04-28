"""
SPARQL → Ground Truth CSV Pipeline
====================================

Fetches structured data from the Wikidata SPARQL endpoint and writes a clean
CSV suitable for use as a PKDB-Bench ground truth table.

Usage
-----
    # Run a query from a .sparql file
    python datasets/build_benchmark/sparql_to_csv.py \\
        --query datasets/build_benchmark/sparql_queries/countries_landlocked.sparql \\
        --out datasets/pkdb_bench/tables/countries_landlocked.csv \\
        --table_id countries_landlocked

    # Run an inline query string
    python datasets/build_benchmark/sparql_to_csv.py \\
        --query_str "SELECT ?country ?pop WHERE { ... }" \\
        --out /tmp/test.csv

    # Preview without saving
    python datasets/build_benchmark/sparql_to_csv.py \\
        --query datasets/build_benchmark/sparql_queries/countries_landlocked.sparql \\
        --preview

Output
------
- CSV file at --out path
- Prints row count and column summary
- Optionally appends metadata entry to datasets/pkdb_bench/cfg.json
"""

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Optional

import pandas as pd
import requests

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "PKDB-Bench/1.0 (research benchmark; contact: pkdb-bench)"

# Label suffix that Wikidata SPARQL adds for human-readable names
_LABEL_SUFFIX = "Label"


def run_sparql(query: str, timeout: int = 60) -> List[Dict]:
    """
    Execute a SPARQL SELECT query against Wikidata and return results as a list of dicts.
    Handles rate limiting with one retry.
    """
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": USER_AGENT,
    }
    params = {"query": query, "format": "json"}

    for attempt in range(2):
        try:
            resp = requests.get(WIKIDATA_ENDPOINT, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                print(f"  Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            bindings = data["results"]["bindings"]
            return bindings
        except requests.exceptions.Timeout:
            print(f"  Timeout on attempt {attempt + 1}. Retrying...")
            time.sleep(5)
        except Exception as e:
            print(f"  SPARQL error: {e}")
            raise

    raise RuntimeError("SPARQL query failed after retries.")


def bindings_to_dataframe(bindings: List[Dict], rename: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """
    Convert Wikidata SPARQL result bindings to a pandas DataFrame.

    - Strips Wikidata URI prefixes (http://www.wikidata.org/entity/Q...)
    - Removes duplicate *Label columns when both ?x and ?xLabel exist (keeps label)
    - Applies optional column rename dict: {sparql_var: desired_column_name}
    """
    if not bindings:
        return pd.DataFrame()

    rows = []
    for b in bindings:
        row = {}
        for var, cell in b.items():
            value = cell.get("value", "")
            # Strip Wikidata entity URIs — keep only the QID if no label available
            if cell.get("type") == "uri" and "wikidata.org/entity/" in value:
                value = value.split("/entity/")[-1]
            row[var] = value
        rows.append(row)

    df = pd.DataFrame(rows)

    # Prefer *Label column over raw *QID column when both exist
    cols_to_drop = []
    for col in list(df.columns):
        if col.endswith(_LABEL_SUFFIX):
            base = col[: -len(_LABEL_SUFFIX)]
            if base in df.columns:
                # Replace base column values with label values, then drop label col
                df[base] = df[col]
                cols_to_drop.append(col)
    df = df.drop(columns=cols_to_drop)

    # Apply rename map
    if rename:
        df = df.rename(columns=rename)

    # Post-process: rows where label column still contains a QID (e.g. "Q7186") mean
    # the Wikidata label service failed for that entity. Drop such rows and warn.
    import re as _re
    _qid_pat = _re.compile(r"^Q\d+$")
    for col in df.columns:
        qid_mask = df[col].astype(str).str.match(_qid_pat)
        if qid_mask.any():
            n = qid_mask.sum()
            print(f"  [WARNING] Column '{col}': {n} row(s) have unresolved QID labels — "
                  f"Wikidata label service fallback. Keeping rows; inspect manually.")

    return df


def clean_dataframe(df: pd.DataFrame, drop_duplicates: bool = True) -> pd.DataFrame:
    """
    Basic cleaning:
    - Strip whitespace from string columns
    - Drop fully-empty rows
    - Drop duplicate rows
    """
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].str.strip()
    df = df.dropna(how="all")
    if drop_duplicates:
        df = df.drop_duplicates()
    df = df.reset_index(drop=True)
    return df


def save_csv(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    print(f"  Saved {len(df)} rows × {len(df.columns)} columns → {path}")


def append_cfg_entry(cfg_path: str, table_id: str, entry: Dict) -> None:
    """Add or update a table entry in a pkdb_bench cfg.json."""
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            cfg = json.load(f)
    else:
        cfg = {}
    cfg[table_id] = entry
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=4)
    print(f"  Updated cfg.json entry: {table_id}")


# ---------------------------------------------------------------------------
# Example queries — callable from Python for testing
# ---------------------------------------------------------------------------

EXAMPLE_QUERIES = {
    # set_filter: countries that drive on the left
    # P1622 = driving side; Q11920728 = left-hand traffic; Q14565199 = right-hand traffic
    "drive_left": {
        "sparql": """
SELECT ?countryLabel WHERE {
  ?country wdt:P31 wd:Q6256 .
  ?country wdt:P1622 wd:Q11920728 .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY ?countryLabel
""",
        "rename": {"country": "Country"},
        "description": "Countries that drive on the left side of the road",
        "query_category": "set_filter",
        "db_analog": "SELECT country FROM countries WHERE traffic_side = 'left'",
    },

    # set_filter: landlocked countries with continent
    # P31=Q123480 (landlocked country) is the cleanest filter
    "landlocked_countries": {
        "sparql": """
SELECT ?countryLabel ?continentLabel WHERE {
  ?country wdt:P31 wd:Q123480 .          # instance of: landlocked country
  ?country wdt:P30 ?continent .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY ?continentLabel ?countryLabel
""",
        "rename": {"country": "Country", "continent": "Continent"},
        "description": "Landlocked countries of the world with their continent",
        "query_category": "set_filter",
        "db_analog": "SELECT country, continent FROM countries WHERE is_landlocked = true",
    },

    # multi_hop: countries bordering BOTH France and Germany, official language not French/German
    # P47=borders, P37=official language, Q150=French, Q188=German
    "france_germany_border_non_french_german": {
        "sparql": """
SELECT DISTINCT ?countryLabel ?languageLabel WHERE {
  wd:Q142 wdt:P47 ?country .
  wd:Q183 wdt:P47 ?country .
  ?country wdt:P31 wd:Q6256 .
  ?country wdt:P37 ?language .
  FILTER(?language != wd:Q150)
  FILTER(?language != wd:Q188)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY ?countryLabel ?languageLabel
""",
        "rename": {"country": "Country", "language": "Official Language"},
        "description": "Countries bordering both France and Germany, official language excl. French and German",
        "query_category": "multi_hop",
        "db_analog": "SELECT c.country, l.language FROM borders b1 JOIN borders b2 ON b1.country=b2.country JOIN official_languages l ON l.country=b1.country WHERE b1.neighbor='France' AND b2.neighbor='Germany' AND l.language NOT IN ('French','German')",
    },

    # set_operation (INTERSECT): people who received Nobel Prize in Physics AND Chemistry
    # Select ?person and ?personLabel so wikibase:label resolves correctly
    "nobel_physics_and_chemistry": {
        "sparql": """
SELECT DISTINCT ?person ?personLabel WHERE {
  ?person wdt:P166 wd:Q38104 .
  ?person wdt:P166 wd:Q44585 .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY ?personLabel
""",
        "rename": {"person": "Laureate"},
        "description": "People who received both the Nobel Prize in Physics and in Chemistry",
        "query_category": "set_operation",
        "db_analog": "SELECT laureate FROM nobel_physics INTERSECT SELECT laureate FROM nobel_chemistry",
    },

    # top_k: cities with population > 5M, with continent — apply top-K per continent in post-processing
    # P31/P279*=Q515 city hierarchy is slow; use P31=Q1549591 (big city) or just P1082 directly
    "large_cities_by_continent": {
        "sparql": """
SELECT ?continentLabel ?cityLabel ?population WHERE {
  ?city wdt:P31 wd:Q1549591 .
  ?city wdt:P1082 ?population .
  ?city wdt:P30 ?continent .
  FILTER(?population > 3000000)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY ?continentLabel DESC(?population)
""",
        "rename": {"continent": "Continent", "city": "City", "population": "Population"},
        "description": "Cities with population > 3M grouped by continent (apply TOP K per continent in post-processing)",
        "query_category": "top_k",
        "db_analog": "SELECT continent, city, population FROM cities WHERE population > 3M ORDER BY continent, population DESC",
        "note": "Apply TOP K per continent in post-processing after fetching",
    },

    # aggregation: Nobel Prize count per country (all categories)
    # P166=award received; filter to Nobel prize items via P31=Q7191
    "nobel_counts_per_country": {
        "sparql": """
SELECT ?countryLabel (COUNT(DISTINCT ?person) AS ?nobel_count) WHERE {
  ?person wdt:P166 ?award .
  ?award wdt:P31 wd:Q7191 .
  ?person wdt:P27 ?country .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
GROUP BY ?countryLabel
ORDER BY DESC(?nobel_count)
LIMIT 50
""",
        "rename": {"country": "Country", "nobel_count": "Nobel Prize Count"},
        "description": "Total Nobel Prize count per country of citizenship (all categories, top 50)",
        "query_category": "aggregation",
        "db_analog": "SELECT country, COUNT(DISTINCT laureate) AS nobel_count FROM nobel_laureates JOIN citizenship ON laureate GROUP BY country ORDER BY nobel_count DESC LIMIT 50",
    },
}


def run_example(name: str, out_dir: str, preview: bool = False) -> pd.DataFrame:
    """Run a named example query and optionally save to out_dir."""
    if name not in EXAMPLE_QUERIES:
        raise ValueError(f"Unknown example: {name}. Available: {list(EXAMPLE_QUERIES)}")

    q = EXAMPLE_QUERIES[name]
    print(f"\n[{name}] {q['description']}")
    print(f"  Category: {q['query_category']}  |  DB analog: {q['db_analog']}")

    bindings = run_sparql(q["sparql"])
    df = bindings_to_dataframe(bindings, rename=q.get("rename"))
    df = clean_dataframe(df)

    print(f"  Result: {len(df)} rows × {list(df.columns)}")
    print(df.head(10).to_string(index=False))

    if not preview:
        out_path = os.path.join(out_dir, f"{name}.csv")
        save_csv(df, out_path)

    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SPARQL → Ground Truth CSV for PKDB-Bench")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", type=str, help="Path to .sparql file")
    group.add_argument("--query_str", type=str, help="Inline SPARQL query string")
    group.add_argument("--example", type=str,
                       help=f"Run a named example query. Options: {list(EXAMPLE_QUERIES)}")
    group.add_argument("--run_all_examples", action="store_true",
                       help="Run all example queries and save to --out_dir")

    parser.add_argument("--out", type=str, default=None, help="Output CSV path")
    parser.add_argument("--out_dir", type=str,
                        default="datasets/pkdb_bench/tables",
                        help="Output directory (used with --example / --run_all_examples)")
    parser.add_argument("--rename", type=str, default=None,
                        help='JSON dict mapping SPARQL var names to column names. E.g. \'{"countryLabel":"Country"}\'')
    parser.add_argument("--table_id", type=str, default=None,
                        help="Table ID for cfg.json entry")
    parser.add_argument("--cfg", type=str,
                        default="datasets/pkdb_bench/cfg.json",
                        help="Path to cfg.json to append metadata entry")
    parser.add_argument("--preview", action="store_true",
                        help="Print results without saving")
    parser.add_argument("--timeout", type=int, default=60,
                        help="SPARQL query timeout in seconds")
    args = parser.parse_args()

    rename = json.loads(args.rename) if args.rename else None

    if args.run_all_examples:
        for name in EXAMPLE_QUERIES:
            try:
                run_example(name, args.out_dir, preview=args.preview)
            except Exception as e:
                print(f"  ERROR running {name}: {e}")
        return

    if args.example:
        run_example(args.example, args.out_dir, preview=args.preview)
        return

    # File or inline query
    if args.query:
        with open(args.query) as f:
            query = f.read()
    else:
        query = args.query_str

    print("Running SPARQL query...")
    bindings = run_sparql(query, timeout=args.timeout)
    df = bindings_to_dataframe(bindings, rename=rename)
    df = clean_dataframe(df)

    print(f"Result: {len(df)} rows × {list(df.columns)}")
    print(df.head(10).to_string(index=False))

    if not args.preview:
        if not args.out:
            print("ERROR: --out required when not using --preview.")
            sys.exit(1)
        save_csv(df, args.out)


if __name__ == "__main__":
    main()
