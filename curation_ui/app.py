import copy
import csv
import io
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
PKDB_DIR = ROOT / "datasets" / "pkdb_bench"
CFG_PATH = PKDB_DIR / "cfg.json"
TABLES_DIR = PKDB_DIR / "tables"
STAGING_DIR = PKDB_DIR / "staging"
REVIEW_LOGS_DIR = PKDB_DIR / "review_logs"
CATEGORIES_PATH = ROOT / "md_claude_code" / "pkdb_bench" / "categories.txt"

APP_TITLE = "PKDB-Bench Curation UI"
CHAT_REQUEST_TIMEOUT_SECONDS = 60
SOURCE_FETCH_TIMEOUT_SECONDS = 15
MAX_SOURCE_CHARS_PER_URL = 4000
MAX_TOTAL_SOURCE_CONTEXT_CHARS = 20000

CHAT_PROVIDERS = {
    "Anthropic": {
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key_session": "anthropic_api_key",
        "models": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
    },
    "OpenAI": {
        "api_key_env": "OPENAI_API_KEY",
        "api_key_session": "openai_api_key",
        "models": ["gpt-5.4", "gpt-5.4-mini", "gpt-4o"],
    },
}

TEMPORAL_SENSITIVITY = ["low", "medium", "high"]
OPERATION_TYPES = [
    "join",
    "union",
    "intersect",
    "projection",
    "union+filter",
    "filter",
    "aggregation",
]

SOURCE_DOMAIN_HINTS = {
    "sparql_wikidata": "query.wikidata.org",
    "manual_wikipedia": "wikipedia.org",
}

COMMENT_TEMPLATE = {
    "doubts": [],
    "comments": [],
    "chat_events": [],
    "chat_summary": "",
    "chat_history": [],
}

CURATION_TEMPLATE = {
    "collection_summary": "",
    "source_coverage_notes": "",
    "decision_log": "",
    "normalization_decisions": "",
    "open_questions": "",
}

SELECT_COL = "Select"
DOUBT_COL = "Doubt"
COMMENT_COUNT_COL = "Comment count"

SUGGESTED_PROMPTS = {
    "factual_lookup": 'Stage a factual_lookup instance with one stable non-time-sensitive attribute and one supporting source URL.',
    "set_filter": 'Stage a set_filter instance with a clearly defined predicate and a source-grounded row set.',
    "implicit_join": 'Stage an implicit_join instance that combines two authoritative source tables on a shared entity key.',
    "aggregation": 'Stage an aggregation instance from a clean authoritative source using a derived group-level statistic.',
    "top_k": 'Stage a top_k instance: top 20 countries by renewable energy share, sourced from IEA or Our World in Data.',
    "set_operation": 'Stage a set_operation instance that unions or intersects two authoritative source lists.',
    "multi_hop": 'Stage a multi_hop instance that traverses two linked facts, such as country -> border -> language.',
    "qualitative": 'Stage a qualitative instance anchored to a named index or report with explicit source URLs.',
    "temporal": 'Stage a temporal instance with a clear as-of date and historically anchored source snapshot.',
    "geographic": 'Stage a geographic instance grounded in an authoritative geographic definition or boundary source.',
    "causal": 'Stage a causal instance anchored to a named report or index and document the grounding source explicitly.',
    "comparative": 'Stage a comparative instance where the same attribute is compared across entities from a stable source.',
    "hierarchical_decomposition": 'Stage a hierarchical_decomposition instance with a large result set that naturally partitions by hierarchy.',
    "multi_attribute_projection": 'Stage a multi_attribute_projection instance with multiple non-key attributes and stable source grounding.',
    "scaled_enumeration": 'Stage a scaled_enumeration instance that partitions a large universe and cites all authoritative sources used.',
}

CHAT_PROMPT_LIBRARY = {
    "Detect errors": (
        "Verify this staged table against the cited source set only. Do not trust the current rows by default and do not answer from parametric memory alone. "
        "Audit the table row-by-row against the current source URLs, cite each source URL and locator, and flag missing, incorrect, duplicate, unsupported, or over-expanded rows relative to that cited source set. "
        "Distinguish factual cell errors from scope errors and source-mismatch errors. If you propose table edits, include a valid source citation block and a full table_update block."
    ),
    "Authoritative-source integrity sweep": (
        "Run a data-integrity sweep over this instance using authoritative sources only. Ignore the current cited source set if it is weaker than the benchmark's desired evidentiary standard. "
        "Do all of the following: 1. verify rows against authoritative sources, 2. identify any rows that are unsupported, missing, or incorrect under authoritative grounding, 3. say whether the current instance can be repaired in place or needs a rebuild, "
        "4. distinguish source-authority problems from factual table errors, and 5. propose a corrected table only if the authoritative evidence clearly supports one. "
        "If you propose table edits, include a valid source citation block and a full table_update block."
    ),
    "Row-by-row audit": (
        "Audit every row in the current table under the declared primary key. For each row, say whether it is supported, unsupported, uncertain, or only source-defined rather than exhaustive, "
        "and cite the source URL and locator for that judgment. For supported rows, call out any incorrect non-key cells. End with a concise summary of rows to keep, rows to fix, rows to delete, and rows whose status depends on scope interpretation."
    ),
    "Check completeness": (
        "Assess whether the current table is exhaustive for the stated query scope. Compare the query wording, schema, and source set, identify any scope mismatch, "
        "and say whether the table is truly exhaustive, source-defined but non-exhaustive, under-filled within the claimed scope, or insufficiently grounded. "
        "Estimate what kinds of rows would be missing if the current source set is too weak."
    ),
    "Normalize values": (
        "Check whether the table values follow benchmark normalization rules and the cfg column_types. Focus on key canonicalization, dates, numeric formatting, units, missing values, categorical consistency, "
        "and whether any cells incorrectly pack multiple values, aliases, or parenthetical variants into one field. Propose a corrected table only if changes are needed."
    ),
    "Detect duplicates": (
        "Find any duplicate or near-duplicate rows under the declared primary key. Explain whether each duplicate is a true error, an alias/canonicalization issue, "
        "an entity-resolution failure, a partition-overlap artifact, or a legitimate distinct row. Propose a corrected table if fixes are needed."
    ),
    "Estimate expected row count": (
        "Estimate the expected row count for this query and source set. Use the query wording, schema, source URLs, any visible source excerpts, and any implied partition structure "
        "to infer a plausible expected range, an approximate best estimate, and whether the current table is likely under-complete, over-complete, source-defined, or roughly correct. "
        "Explain the reasoning behind the estimate rather than giving only a number."
    ),
    "Cardinality and coverage audit": (
        "Audit cardinality and coverage for this instance. Check row count, unique primary-key count, duplicate rate, missing expected entities, and partition balance when relevant. "
        "For partitioned top-k or hierarchical queries, validate both total and per-partition counts plus the ranking/filtering convention used inside each partition. "
        "Flag omitted higher-ranked or in-scope entities even when the whole table has the expected total row count. "
        "If the cited source set itself uses partition-specific conventions, assess whether the staged claim type, title, and db_analog honestly describe that source-defined scope. "
        "Decide whether the current table appears exhaustive, source-defined but incomplete, or over-expanded beyond the sources. Return both a count-based summary and a plain-language judgment, "
        "including whether the current table shape is plausible for a benchmark instance."
    ),
    "Partition coverage audit": (
        "If the query is implicitly partitioned (for example by continent, state, year, category, hierarchy level, or source group), audit whether coverage is balanced and consistent across partitions. "
        "Identify partitions that appear missing, under-filled, over-filled, duplicated across boundaries, or inconsistently interpreted, especially when qualitative predicates might drift across partitions."
    ),
    "Schema and table-shape audit": (
        "Audit whether this instance has the right schema and table shape for PKDB-Bench. Check whether the current columns, primary key, and row granularity match the query intent; "
        "whether any fields should be split, normalized, or removed; whether any non-key attribute is missing; and whether the output is truly a rectangular table rather than a packed list or scalar disguised as a table. "
        "If the current schema is not ideal, propose an improved schema and explain how the current table should be migrated."
    ),
    "Validate metadata": (
        "Review the benchmark registration draft against the table and sources using the current benchmark protocol, not an idealized external standard. "
        "Treat approved source tiers as acceptable when they are honestly declared in metadata, and focus on whether the instance is accurate and non-misleading under current policy. "
        "Check whether table_title, query_category, db_analog, operation_type, primary_key, column_types, source_count, domain, temporal_sensitivity, gt_construction, temporal_note, and source_urls accurately describe the current instance. "
        "Separate blocking metadata issues under current policy from non-blocking improvement suggestions, and flag any metadata that would mislead later benchmark analysis."
    ),
    "Suggest db_analog": (
        "Propose a SQL db_analog expression for this table based on its query_category, schema, and structure. "
        "Explain the choice of operations, whether the query is really a join / union / filter / aggregation / top-k / projection pattern, and whether the current db_analog accurately reflects the semantics instead of only the surface wording."
    ),
    "Summarize source": (
        "Summarize what each cited source URL contributes to this instance and whether the current table accurately represents those sources. "
        "Be explicit about which source supports which rows or attributes, and flag any mismatch between source content, merge logic, and the staged rows."
    ),
    "Challenge the source": (
        "Act as a skeptical benchmark reviewer. Use the current benchmark policy, not an idealized external standard: approved weaker tiers may still be valid when honestly declared. "
        "Identify where the cited sources may be weak, incomplete, non-authoritative, unstable, circular, or mismatched to the query wording. Keep coverage checking rigorous: ask whether the cited sources actually support every row and the claimed scope. "
        "Separate blocking issues from non-blocking source-quality upgrades, and recommend whether the query should be kept, narrowed, rebuilt, reframed as source-defined, or rejected."
    ),
    "Rebuild to authoritative sources": (
        "Assume the current instance concept is worth keeping, but the current source set may be too weak. Propose how to rebuild it from authoritative sources. "
        "Identify better source types or organizations, explain what exact row set those sources could support, say whether the query wording should change, and state whether the rebuilt instance would be exhaustive, source-defined, or still partially supplemented."
    ),
    "Check LLM-knowable": (
        "Assess whether a general-purpose LLM with no tool use and a training cutoff before 2025 would plausibly know this content from broad pretraining alone. "
        "This is about closed-book benchmark fit, not about how the ground truth was curated. Even if the table was built from Wikipedia or from an agentic source-fetching workflow, "
        "judge whether the evaluated model could answer these rows from parametric memory without browsing. Identify rows that are likely obscure, highly niche, post-cutoff, low-prominence, or source-specific, "
        "and conclude whether the instance is a good fit for parametric-knowledge benchmarking rather than a retrieval benchmark."
    ),
    "Confidence / calibration risk audit": (
        "Assess where confidence or calibration risk is likely to concentrate in this instance. Identify columns, row types, partitions, or value classes that are likely to be easy/high-confidence, "
        "hard/low-confidence, or dangerously overconfident if generated by an LLM from parametric knowledge alone. Focus especially on time-sensitive values, numeric fields, ranks, qualitative predicates, "
        "obscure entities, and columns whose confidence should vary independently within the same row. Conclude whether this instance is useful for later calibration-aware benchmark analysis."
    ),
    "Registration readiness": (
        "Decide whether this instance is ready for benchmark registration under the current benchmark protocol. Be policy-faithful, not perfectionist: do not block registration only because stronger sources would be preferable if the current source tier is allowed and honestly declared. "
        "Evaluate table correctness, table-shaped output quality, source grounding, scope clarity, normalization, metadata consistency, LLM-knowable fit, and remaining doubts. "
        "Keep coverage checking strict, and return: Ready / Not ready, followed by 1. blocking issues under current policy, 2. non-blocking upgrade suggestions, 3. the minimal exact fixes needed, and 4. whether the instance would still be publishable as a benchmark example after those fixes."
    ),
}

CHAT_PROMPT_GUIDANCE = {
    "Check LLM-knowable": (
        "Use this only to judge benchmark fit: would a closed-book model likely know these facts from pretraining alone? "
        "The answer can still be 'yes' even if the curation source is Wikipedia, and it can still be 'no' even if an agent could fetch the facts from the web."
    ),
    "Authoritative-source integrity sweep": (
        "Use this only when you intentionally want to hold the instance to a stronger source standard than its current cited URLs. Good for deciding whether to rebuild from ECB / EU / Census / OECD / similar authorities."
    ),
    "Challenge the source": (
        "Best when the main question is whether the cited sources are authoritative enough, complete enough, or properly matched to the query wording."
    ),
    "Rebuild to authoritative sources": (
        "Use after a source challenge when the instance concept seems good but the current sources are too weak for final registration."
    ),
    "Estimate expected row count": (
        "Useful when you suspect the table is too short or too long and want a rough expected-count sanity check before doing a full row-by-row rebuild."
    ),
    "Partition coverage audit": (
        "Use for queries that naturally break into groups such as continent, year, state, category, or source partition."
    ),
    "Schema and table-shape audit": (
        "Use when the main issue is not just bad rows, but whether the chosen columns, key, and row granularity are right for the benchmark at all."
    ),
    "Confidence / calibration risk audit": (
        "Use when you want to predict where later confidence-aware evaluation will be informative or unstable, especially for multi-attribute tables."
    ),
}

CHAT_PROMPT_BUNDLES = {
    "Data integrity sweep": (
        "Run a data-integrity sweep over this instance using the cited source set only for row-level correctness. "
        "In one response, do all of the following:\n"
        "1. detect missing, incorrect, duplicate, or unsupported rows relative to the cited source set,\n"
        "2. check whether values are normalized and match the declared column_types,\n"
        "3. flag any near-duplicate entities, primary-key issues, or multi-valued cells that break the intended table shape,\n"
        "4. estimate whether the current total row count, per-partition row count when relevant, and unique-key count look plausible for the stated query and cited source set,\n"
        "5. note whether the Wiki-family source discovery pass found a missing source that could change scope, coverage, ranking, or provenance,\n"
        "6. say whether problems are factual, structural, or scope-related,\n"
        "7. propose a corrected table only if changes are actually needed.\n"
        "If you propose table edits, include a valid source citation block and a full table_update block."
    ),
    "Source and scope sweep": (
        "Review the source grounding and scope of this instance in one pass, using the current benchmark policy rather than an idealized external standard. "
        "Do not equate 'non-authoritative' with 'invalid' if the current source tier is allowed and honestly declared, but keep coverage checks rigorous: assess whether the cited sources actually support every row and the claimed scope. "
        "Before concluding, run a bounded Wiki-family source discovery pass (linked/sibling Wikipedia pages, individual entity pages, Wikidata pages, or SPARQL sanity checks) and decide whether the staged source set is missing a more direct Wikipedia or Wikidata source. "
        "In one response, do all of the following:\n"
        "1. summarize what each cited source contributes,\n"
        "2. assess whether the table is complete for the stated query scope,\n"
        "3. estimate the expected coverage or row-count range implied by the sources, including per-partition counts for partitioned queries,\n"
        "4. identify any mismatch between the query wording and what the sources actually support,\n"
        "5. identify any missing Wiki-family source that materially affects row universe, ranking rule, partition boundary, or provenance,\n"
        "6. challenge weak, unstable, or non-authoritative sources,\n"
        "7. decide whether the instance is exhaustive, source-defined, or under-grounded,\n"
        "8. recommend whether the query should be kept, narrowed, rebuilt, or rejected, clearly separating blocking issues from non-blocking stronger-source upgrades."
    ),
    "Metadata and registration sweep": (
        "Perform a metadata and registration-readiness sweep using the current benchmark protocol as the decision standard. "
        "Be policy-faithful, not perfectionist: if a source tier is allowed by the protocol and honestly declared in metadata, do not treat 'stronger sources would be better' as a blocker by itself. "
        "Separate blocking issues under current policy from non-blocking improvement suggestions. "
        "In one response, do all of the following:\n"
        "1. validate the benchmark registration draft against the table and sources,\n"
        "2. assess whether db_analog, query_category, operation_type, primary_key, column_types, gt_construction, temporal_note, temporal_sensitivity, source_count, and source_urls are correct,\n"
        "3. assess whether the current total and per-partition row count / expected cardinality story is documented clearly enough for registration,\n"
        "4. assess whether the content is appropriate for LLM parametric-knowledge benchmarking,\n"
        "5. check whether the instance would support later per-type and calibration analysis without misleading metadata,\n"
        "6. return Ready / Not ready,\n"
        "7. list the blocking issues and the minimal exact field changes needed."
    ),
    "Full pre-registration sweep": (
        "Perform a full pre-registration review of this staged instance using the cited source set and current benchmark policy. "
        "Be policy-faithful, not perfectionist: if the source tier is allowed and honestly declared, do not block registration only because stronger sources could exist. "
        "At the same time, keep coverage checking strict and assess whether the cited sources truly support the rows and claimed scope. "
        "Run a bounded Wiki-family source discovery pass (linked/sibling Wikipedia pages, individual entity pages, Wikidata pages, or SPARQL sanity checks) so missing Wikipedia/Wikidata sources are surfaced before broader web validation. "
        "Cover all of the following in one response:\n"
        "1. row-level correctness and unsupported/missing rows relative to the cited source set,\n"
        "2. duplicates and primary-key problems,\n"
        "3. normalization, table-shape quality, and column-type consistency,\n"
        "4. expected total and per-partition row-count / cardinality estimate, partition sanity, and unique-key sanity,\n"
        "5. missing Wiki-family source check: whether a more direct Wikipedia or Wikidata source exists and would change scope, coverage, ranking, or provenance,\n"
        "6. source quality, source tier, and source-to-query scope match,\n"
        "7. table completeness for the intended scope,\n"
        "8. cfg metadata correctness,\n"
        "9. LLM-knowable fit for the benchmark,\n"
        "10. whether this instance helps the benchmark's intended multi-source / structured extraction story,\n"
        "11. final Ready / Not ready judgment, clearly separating blocking issues under current policy from non-blocking upgrade suggestions.\n"
        "If any table corrections are needed, include one valid source citation block and one full table_update block, then separately list the recommended cfg field changes."
    ),
}

CHAT_BUNDLE_GUIDANCE = {
    "Data integrity sweep": "Best first pass after staging or major edits. Checks row correctness, duplicates, and normalization in one run.",
    "Source and scope sweep": "Use when the main risk is whether the cited sources actually support the query wording and intended benchmark scope.",
    "Metadata and registration sweep": "Use near the end to verify cfg metadata, benchmark fit, and whether the instance is ready to register.",
    "Full pre-registration sweep": "Closest to test all: one broad pass covering data integrity, scope, metadata, benchmark fit, and a final ready/not-ready judgment.",
}


def render_layout_css() -> None:
    st.markdown(
        """
<style>
div[data-testid="stHorizontalBlock"]:first-of-type > div[data-testid="column"]:last-child {
    position: sticky;
    top: 0.75rem;
    align-self: flex-start;
}
</style>
""",
        unsafe_allow_html=True,
    )


def load_env_value(env_name: str) -> Tuple[str, Optional[str]]:
    direct = os.environ.get(env_name, "").strip()
    if direct:
        return direct, "environment"

    zshrc_path = Path.home() / ".zshrc"
    if not zshrc_path.exists():
        return "", None

    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(env_name)}=(.+?)\s*$")
    for line in zshrc_path.read_text().splitlines():
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if "#" in value and not value.startswith(("'", '"')):
            value = value.split("#", 1)[0].strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if value:
            return value, str(zshrc_path)
    return "", None


def ensure_directories() -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_LOGS_DIR.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return copy.deepcopy(default)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")


def load_cfg() -> Dict[str, Dict[str, Any]]:
    return read_json(CFG_PATH, {})


def save_cfg(cfg: Dict[str, Dict[str, Any]]) -> None:
    write_json(CFG_PATH, cfg)


def parse_categories() -> List[str]:
    categories: List[str] = []
    if not CATEGORIES_PATH.exists():
        return categories
    for line in CATEGORIES_PATH.read_text().splitlines():
        if not line.startswith("│"):
            continue
        parts = line.split("│")
        if len(parts) < 3:
            continue
        category = parts[1].strip()
        if not category or category == "Category":
            continue
        if set(category) <= {"─", " "}:
            continue
        categories.append(category)
    return categories or list(SUGGESTED_PROMPTS.keys())


def list_staged_instance_ids() -> List[str]:
    ids = set()
    for path in STAGING_DIR.glob("*.draft.json"):
        ids.add(path.name.replace(".draft.json", ""))
    for path in STAGING_DIR.glob("*.csv"):
        ids.add(path.stem)
    return sorted(ids)


def default_comments() -> Dict[str, Any]:
    return copy.deepcopy(COMMENT_TEMPLATE)


def default_curation() -> Dict[str, Any]:
    return copy.deepcopy(CURATION_TEMPLATE)


def load_staged_instance(instance_id: str) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    csv_path = STAGING_DIR / f"{instance_id}.csv"
    draft_path = STAGING_DIR / f"{instance_id}.draft.json"
    comments_path = STAGING_DIR / f"{instance_id}.comments.json"
    curation_path = STAGING_DIR / f"{instance_id}.curation.json"

    df = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame()
    df = strip_ui_columns(df)
    draft = read_json(draft_path, {})
    comments = read_json(comments_path, default_comments())
    curation = read_json(curation_path, default_curation())
    comments.setdefault("doubts", [])
    comments.setdefault("comments", [])
    comments.setdefault("chat_events", [])
    comments.setdefault("chat_summary", "")
    comments.setdefault("chat_history", [])
    for key, value in CURATION_TEMPLATE.items():
        curation.setdefault(key, copy.deepcopy(value))
    return df, draft, comments, curation


def save_staged_instance(instance_id: str, df: pd.DataFrame, draft: Dict[str, Any], comments: Dict[str, Any], curation: Dict[str, Any]) -> None:
    strip_ui_columns(df).to_csv(STAGING_DIR / f"{instance_id}.csv", index=False)
    write_json(STAGING_DIR / f"{instance_id}.draft.json", draft)
    write_json(STAGING_DIR / f"{instance_id}.comments.json", comments)
    write_json(STAGING_DIR / f"{instance_id}.curation.json", curation)


def delete_staged_instance(instance_id: str) -> None:
    for suffix in [".csv", ".draft.json", ".comments.json", ".curation.json"]:
        path = STAGING_DIR / f"{instance_id}{suffix}"
        if path.exists():
            path.unlink()


def clean_registered_entry(draft: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in draft.items() if not k.startswith("_")}


def build_review_log(instance_id: str, draft: Dict[str, Any], comments: Dict[str, Any], curation: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": instance_id,
        "registered_at": now_iso(),
        "registered_by": "human",
        "staged_by": draft.get("_staged_by", "unknown"),
        "staged_at": draft.get("_staged_at", ""),
        "extraction_notes": draft.get("_extraction_notes", ""),
        "source_urls_at_registration": draft.get("source_urls", []),
        "temporal_note_at_registration": draft.get("temporal_note", ""),
        "chat_events": comments.get("chat_events", []),
        "resolved_doubts": [],
        "open_doubts_overridden": comments.get("doubts", []),
        "comments": comments.get("comments", []),
        "curation_record": curation,
    }


def register_instance(instance_id: str, df: pd.DataFrame, draft: Dict[str, Any], comments: Dict[str, Any], curation: Dict[str, Any]) -> None:
    cfg = load_cfg()
    cfg[instance_id] = clean_registered_entry(draft)
    save_cfg(cfg)
    df.to_csv(TABLES_DIR / f"{instance_id}.csv", index=False)
    write_json(REVIEW_LOGS_DIR / f"{instance_id}.review.json", build_review_log(instance_id, draft, comments, curation))
    delete_staged_instance(instance_id)


def build_row_flags(df: pd.DataFrame, comments: Dict[str, Any], selected_rows: Optional[List[int]] = None) -> pd.DataFrame:
    display_df = df.copy()
    selected_rows = selected_rows or []
    doubts_by_row = Counter(d["row"] for d in comments.get("doubts", []) if isinstance(d.get("row"), int))
    comments_by_row = Counter(
        c["row"]
        for c in comments.get("comments", [])
        if c.get("scope") in {"row", "cell"} and isinstance(c.get("row"), int)
    )
    display_df[SELECT_COL] = [i in selected_rows for i in range(len(display_df))]
    display_df[DOUBT_COL] = [doubts_by_row.get(i, 0) > 0 for i in range(len(display_df))]
    display_df[COMMENT_COUNT_COL] = [comments_by_row.get(i, 0) for i in range(len(display_df))]
    return display_df


def strip_ui_columns(df: pd.DataFrame) -> pd.DataFrame:
    helper_cols = {SELECT_COL, DOUBT_COL, COMMENT_COUNT_COL, "__selected__", "__doubt__", "__comment_count__", "⚑", "💬"}
    cols = [c for c in df.columns if c not in helper_cols and not c.startswith("__")]
    return df[cols].copy()


def sync_doubts_from_editor(comments: Dict[str, Any], original_df: pd.DataFrame, edited_df: pd.DataFrame) -> Dict[str, Any]:
    updated = copy.deepcopy(comments)
    existing_by_row = defaultdict(list)
    for item in updated.get("doubts", []):
        existing_by_row[item.get("row")].append(item)

    new_doubts: List[Dict[str, Any]] = []
    for row_idx in range(len(edited_df)):
        flagged = bool(edited_df.iloc[row_idx].get(DOUBT_COL, False) or edited_df.iloc[row_idx].get("__doubt__", False))
        if flagged:
            row_items = existing_by_row.get(row_idx)
            if row_items:
                new_doubts.extend(row_items)
            else:
                new_doubts.append({"row": row_idx, "col": "", "reason": ""})
    updated["doubts"] = new_doubts
    return updated


def get_comment_preview(comments: Dict[str, Any], scope: str, row_idx: Optional[int], column: str) -> str:
    for item in comments.get("comments", []):
        if item.get("scope") == scope and item.get("row") == row_idx and item.get("column", "") == column:
            return item.get("text", "")
    return ""


def upsert_comment(comments: Dict[str, Any], scope: str, row_idx: Optional[int], column: str, text: str) -> Dict[str, Any]:
    updated = copy.deepcopy(comments)
    new_comments = []
    found = False
    for item in updated.get("comments", []):
        if item.get("scope") == scope and item.get("row") == row_idx and item.get("column", "") == column:
            found = True
            if text.strip():
                new_comments.append({"scope": scope, "row": row_idx, "column": column, "text": text.strip()})
        else:
            new_comments.append(item)
    if not found and text.strip():
        new_comments.append({"scope": scope, "row": row_idx, "column": column, "text": text.strip()})
    updated["comments"] = new_comments
    return updated


def upsert_comment_many(comments: Dict[str, Any], scope: str, rows: List[int], columns: List[str], text: str) -> Dict[str, Any]:
    updated = copy.deepcopy(comments)
    if scope == "cell":
        targets = [(row, column) for row in rows for column in columns]
    elif scope == "row":
        targets = [(row, "") for row in rows]
    else:
        targets = [(None, column) for column in columns]
    for row_idx, column in targets:
        updated = upsert_comment(updated, scope, row_idx, column, text)
    return updated


def comments_dataframe(comments: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    for idx, item in enumerate(comments.get("comments", [])):
        rows.append(
            {
                "comment_id": idx,
                "scope": item.get("scope", ""),
                "row": item.get("row"),
                "column": item.get("column", ""),
                "text": item.get("text", ""),
            }
        )
    return pd.DataFrame(rows)


def update_comment_by_index(comments: Dict[str, Any], comment_id: int, new_text: str) -> Dict[str, Any]:
    updated = copy.deepcopy(comments)
    if 0 <= comment_id < len(updated.get("comments", [])):
        if new_text.strip():
            updated["comments"][comment_id]["text"] = new_text.strip()
        else:
            updated["comments"].pop(comment_id)
    return updated


def remove_comments_by_indices(comments: Dict[str, Any], comment_ids: List[int]) -> Dict[str, Any]:
    updated = copy.deepcopy(comments)
    remove_set = {int(i) for i in comment_ids}
    updated["comments"] = [item for idx, item in enumerate(updated.get("comments", [])) if idx not in remove_set]
    return updated


def parse_year_present(text: str) -> bool:
    return bool(re.search(r"\b(19|20)\d{2}\b", text or ""))


def validate_gt_construction_alignment(gt_construction: str, source_urls: List[str]) -> Optional[str]:
    if gt_construction.startswith("sparql_wikidata") and not any("query.wikidata.org" in url for url in source_urls):
        return "gt_construction says sparql_wikidata but no Wikidata query URL was provided."
    if gt_construction.startswith("web_search_") and any("wikipedia.org" in url for url in source_urls):
        return "gt_construction says web_search_* but one or more source URLs are Wikipedia."
    if gt_construction == "manual_wikipedia" and source_urls and not all("wikipedia.org" in url for url in source_urls):
        return "gt_construction says manual_wikipedia but at least one source URL is not Wikipedia."
    return None


def validate_operation_query_pair(operation_type: str, query_category: str) -> Optional[str]:
    allowed = {
        "set_operation": {"union", "intersect", "union+filter"},
        "implicit_join": {"join"},
        "multi_attribute_projection": {"projection", "join"},
        "set_filter": {"filter"},
        "aggregation": {"aggregation"},
        "top_k": {"projection", "filter", "union+filter"},
        "multi_hop": {"join", "filter"},
        "temporal": {"filter", "projection", "join"},
        "geographic": {"filter"},
        "causal": {"projection", "aggregation", "filter"},
        "comparative": {"projection", "join"},
        "hierarchical_decomposition": {"join", "projection"},
        "scaled_enumeration": {"projection", "union+filter"},
        "factual_lookup": {"projection"},
        "qualitative": {"filter", "projection"},
    }
    allowed_ops = allowed.get(query_category)
    if allowed_ops and operation_type not in allowed_ops:
        return f"operation_type={operation_type} is unusual for query_category={query_category}."
    return None


def validate_state(instance_id: str, df: pd.DataFrame, draft: Dict[str, Any], comments: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    columns = draft.get("columns", [])
    primary_key = draft.get("primary_key", [])
    column_types = draft.get("column_types", {})
    source_urls = [u for u in draft.get("source_urls", []) if str(u).strip()]

    if len(df) < 10:
        errors.append("Rows >= 10")
    if primary_key:
        try:
            if int(df.duplicated(subset=primary_key).sum()) > 0:
                errors.append("Primary key unique")
        except KeyError:
            errors.append("Primary key columns missing from table")
    if list(df.columns) != columns:
        errors.append("CSV columns match cfg columns")
    if any(col not in column_types for col in columns):
        errors.append("column_types complete")
    if instance_id in cfg:
        errors.append("id not already in cfg.json")
    if instance_id != draft.get("id", ""):
        errors.append("id matches staging filename")
    if not source_urls:
        errors.append("source_urls non-empty")
    if not str(draft.get("gt_construction", "")).strip():
        errors.append("gt_construction non-empty")
    if draft.get("source_count") != len(source_urls):
        errors.append("source_count matches source_urls")
    if draft.get("secondary_category") and draft.get("secondary_category") == draft.get("query_category"):
        errors.append("secondary_category must differ from query_category")

    if not parse_year_present(str(draft.get("temporal_note", ""))):
        warnings.append("temporal_note should contain a year")
    if comments.get("doubts"):
        warnings.append("Open doubt flags remain")
    if len(df) > 500:
        warnings.append("Rows > 500; justify in temporal_note")

    gt_msg = validate_gt_construction_alignment(str(draft.get("gt_construction", "")), source_urls)
    if gt_msg:
        warnings.append(gt_msg)
    op_msg = validate_operation_query_pair(str(draft.get("operation_type", "")), str(draft.get("query_category", "")))
    if op_msg:
        warnings.append(op_msg)

    return errors, warnings


def compute_category_coverage(cfg: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
    counts = Counter(item.get("query_category", "") for item in cfg.values())
    targets = {
        "set_operation": 6,
        "implicit_join": 4,
        "multi_attribute_projection": 4,
        "aggregation": 3,
        "set_filter": 3,
        "top_k": 3,
        "multi_hop": 3,
        "temporal": 3,
        "hierarchical_decomposition": 3,
        "comparative": 3,
        "scaled_enumeration": 3,
        "qualitative": 3,
        "geographic": 3,
        "causal": 3,
        "factual_lookup": 2,
    }
    rows = []
    for category in parse_categories():
        target = targets.get(category, 3)
        registered = counts.get(category, 0)
        rows.append(
            {
                "Category": category,
                "Registered": registered,
                "Target": target,
                "Gap": max(target - registered, 0),
            }
        )
    return pd.DataFrame(rows)


def dataframe_to_csv_text(df: pd.DataFrame) -> str:
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue()


def parse_tagged_block(text: str, start_tag: str, end_tag: str) -> Optional[str]:
    pattern = re.compile(re.escape(start_tag) + r"(.*?)" + re.escape(end_tag), re.DOTALL)
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def parse_source_citation(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    block = parse_tagged_block(text, "<!--source_citation-->", "<!--end_source_citation-->")
    if block is None:
        return None, "No source citation block provided."
    try:
        parsed = json.loads(block)
    except json.JSONDecodeError as exc:
        return None, f"Source citation JSON is invalid: {exc}"
    if not isinstance(parsed, dict):
        return None, "Source citation block must be a JSON object."
    sources = parsed.get("sources")
    evidence_map = parsed.get("evidence_map")
    if not isinstance(sources, list) or not isinstance(evidence_map, list):
        return None, "Source citation block must contain sources and evidence_map lists."
    source_ids = {item.get("id") for item in sources if isinstance(item, dict)}
    if None in source_ids or not source_ids:
        return None, "Every source must include a non-empty id."
    for item in evidence_map:
        if not isinstance(item, dict):
            return None, "Every evidence_map entry must be an object."
        if item.get("source_id") not in source_ids:
            return None, "Every evidence_map entry must reference a declared source_id."
    return parsed, None


def parse_table_update_csv(text: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    block = parse_tagged_block(text, "<!--table_update-->", "<!--end_table_update-->")
    if block is None:
        return None, None
    try:
        reader = csv.reader(io.StringIO(block))
        rows = list(reader)
        if not rows:
            return None, "Table update block is empty."
        header = rows[0]
        data = rows[1:]
        return pd.DataFrame(data, columns=header), None
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"Could not parse table update CSV: {exc}"


def fetch_source_excerpt(url: str) -> Tuple[str, Optional[str]]:
    try:
        response = requests.get(url, timeout=SOURCE_FETCH_TIMEOUT_SECONDS, headers={"User-Agent": "PKDB-Bench-Curation-UI/1.0"})
        response.raise_for_status()
    except Exception as exc:
        return "", str(exc)

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{2,}", "\n\n", text)
    excerpt = text[:MAX_SOURCE_CHARS_PER_URL]
    return excerpt, None


def build_source_context_payload(source_urls: List[str]) -> Dict[str, Any]:
    total_chars = 0
    sources = []
    included_count = 0
    budget_skipped_count = 0
    error_count = 0
    for url in source_urls:
        excerpt, error = fetch_source_excerpt(url)
        status = "ok"
        include = True
        if error:
            status = "error"
            error_count += 1
            include = False
        elif total_chars + len(excerpt) > MAX_TOTAL_SOURCE_CONTEXT_CHARS:
            status = "budget_skipped"
            budget_skipped_count += 1
            include = False
        if include:
            total_chars += len(excerpt)
            included_count += 1
        sources.append(
            {
                "url": url,
                "status": status,
                "included_in_prompt": include,
                "excerpt": excerpt if include else "",
                "error": error or "",
            }
        )
    return {
        "source_count": len(source_urls),
        "included_count": included_count,
        "budget_skipped_count": budget_skipped_count,
        "error_count": error_count,
        "sources": sources,
    }


def build_source_context(source_urls: List[str]) -> Tuple[str, List[str], Dict[str, Any]]:
    payload = build_source_context_payload(source_urls)
    parts = []
    errors = []
    for idx, item in enumerate(payload["sources"], start=1):
        if item["included_in_prompt"] and item["excerpt"]:
            parts.append(f"[Source {idx}] {item['url']}\n{item['excerpt']}")
        if item["error"]:
            errors.append(f"{item['url']}: {item['error']}")
    return "\n\n".join(parts), errors, payload


def compute_diff(old_df: pd.DataFrame, new_df: pd.DataFrame, primary_key: Optional[List[str]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    diffs: List[Dict[str, Any]] = []
    shared_cols = [c for c in old_df.columns if c in new_df.columns]
    if primary_key and all(col in old_df.columns for col in primary_key) and all(col in new_df.columns for col in primary_key):
        old_index = {tuple(old_df.iloc[i][col] for col in primary_key): i for i in range(len(old_df))}
        new_index = {tuple(new_df.iloc[i][col] for col in primary_key): i for i in range(len(new_df))}
        for key in sorted(set(old_index) - set(new_index)):
            old_idx = old_index[key]
            diffs.append({"row": old_idx, "column": "__row__", "before": old_df.iloc[old_idx].to_dict(), "after": None, "kind": "row_deleted"})
        for key in sorted(set(new_index) - set(old_index)):
            new_idx = new_index[key]
            diffs.append({"row": new_idx, "column": "__row__", "before": None, "after": new_df.iloc[new_idx].to_dict(), "kind": "row_added"})
        for key in sorted(set(old_index) & set(new_index)):
            old_idx = old_index[key]
            new_idx = new_index[key]
            row_ref = new_idx
            for col in shared_cols:
                before = old_df.iloc[old_idx][col]
                after = new_df.iloc[new_idx][col]
                if str(before) != str(after):
                    diffs.append({"row": row_ref, "column": col, "before": before, "after": after, "kind": "cell_change"})
    else:
        shared_rows = min(len(old_df), len(new_df))
        for row_idx in range(shared_rows):
            for col in shared_cols:
                before = old_df.iloc[row_idx][col]
                after = new_df.iloc[row_idx][col]
                if str(before) != str(after):
                    diffs.append({"row": row_idx, "column": col, "before": before, "after": after, "kind": "cell_change"})
        for row_idx in range(shared_rows, len(new_df)):
            diffs.append({"row": row_idx, "column": "__row__", "before": None, "after": new_df.iloc[row_idx].to_dict(), "kind": "row_added"})
        for row_idx in range(shared_rows, len(old_df)):
            diffs.append({"row": row_idx, "column": "__row__", "before": old_df.iloc[row_idx].to_dict(), "after": None, "kind": "row_deleted"})

    summary = {
        "rows_changed": len({d["row"] for d in diffs if d["kind"] == "cell_change"}),
        "rows_added": sum(1 for d in diffs if d["kind"] == "row_added"),
        "rows_deleted": sum(1 for d in diffs if d["kind"] == "row_deleted"),
    }
    return diffs, summary


def validate_evidence_coverage(diffs: List[Dict[str, Any]], citation: Dict[str, Any]) -> Optional[str]:
    evidence_entries = citation.get("evidence_map", [])
    covered_columns = {entry.get("column") for entry in evidence_entries}
    changed_columns = {d.get("column") for d in diffs}
    missing = sorted(changed_columns - covered_columns)
    if missing:
        return f"Evidence map does not cover all changed columns: {missing}"
    return None


def build_system_prompt(
    instance_id: str,
    draft: Dict[str, Any],
    df: pd.DataFrame,
    comments: Dict[str, Any],
    curation: Dict[str, Any],
    source_context: str = "",
) -> str:
    benchmark_view = {
        "id": instance_id,
        "table_title": draft.get("table_title", ""),
        "query_category": draft.get("query_category", ""),
        "secondary_category": draft.get("secondary_category", ""),
        "db_analog": draft.get("db_analog", ""),
        "operation_type": draft.get("operation_type", ""),
        "source_count": draft.get("source_count", 0),
        "domain": draft.get("domain", ""),
        "temporal_sensitivity": draft.get("temporal_sensitivity", ""),
        "gt_construction": draft.get("gt_construction", ""),
        "temporal_note": draft.get("temporal_note", ""),
        "source_urls": draft.get("source_urls", []),
        "primary_key": draft.get("primary_key", []),
        "columns": draft.get("columns", []),
        "column_types": draft.get("column_types", {}),
    }
    prompt = f"""You are a curation assistant for PKDB-Bench, a benchmark for LLM parametric knowledge extraction.
You help validate and improve ground-truth tables before they are registered.

Benchmark registration view:
{json.dumps(benchmark_view, indent=2)}

Current table (CSV):
{dataframe_to_csv_text(df)}

Current curation record:
{json.dumps(curation, indent=2)}

Current review comments and doubts:
{json.dumps({"doubts": comments.get("doubts", []), "comments": comments.get("comments", [])}, indent=2)}

Rules:
- Ground-truth must come from a real source-grounded external source under the current benchmark protocol, not from your parametric knowledge alone.
- When you propose ANY factual change to the table, you MUST cite the specific source (URL or document name + section) that justifies the change.
- When you propose a table change, output a structured <!--source_citation--> JSON block first.
  It must contain:
  - a sources list with source ids, URLs, and locators/sections when available
  - an evidence_map list with one entry per changed row/cell using the fields row, column, source_id, and justification
  - for row additions or deletions, use column="__row__" for that row
- Then output the full updated CSV inside <!--table_update--> ... <!--end_table_update--> tags.
- When you propose a cfg field change, state the field name, new value, and the reason clearly.
- Treat the benchmark registration view as the current candidate metadata, not as ground truth about the curation process.
- Use the curation record for source interpretation, working assumptions, collection notes, and decision history.
- Flag any row you are uncertain about rather than silently guessing.
- For country/territory key columns, use country_converter short_name for canonical names. Never shorten to abbreviations.
"""

    if source_context:
        prompt += f"""

Fetched source page excerpts:
{source_context}
"""

    return prompt


def ensure_session_defaults(instance_id: str, df: pd.DataFrame, draft: Dict[str, Any], comments: Dict[str, Any], curation: Dict[str, Any]) -> None:
    st.session_state.setdefault("selected_instance", instance_id)
    table_key = f"table_state::{instance_id}"
    draft_key = f"draft_state::{instance_id}"
    comments_key = f"comments_state::{instance_id}"
    curation_key = f"curation_state::{instance_id}"
    selected_rows_key = f"selected_rows::{instance_id}"
    chat_key = f"chat_history::{instance_id}"
    proposal_key = f"proposal::{instance_id}"
    source_review_key = f"source_review::{instance_id}"

    if table_key not in st.session_state:
        st.session_state[table_key] = df.copy()
    if draft_key not in st.session_state:
        st.session_state[draft_key] = copy.deepcopy(draft)
    if comments_key not in st.session_state:
        st.session_state[comments_key] = copy.deepcopy(comments)
    if curation_key not in st.session_state:
        st.session_state[curation_key] = copy.deepcopy(curation)
    if selected_rows_key not in st.session_state:
        st.session_state[selected_rows_key] = []
    if chat_key not in st.session_state:
        st.session_state[chat_key] = copy.deepcopy(comments.get("chat_history", []))
    if proposal_key not in st.session_state:
        st.session_state[proposal_key] = None
    if source_review_key not in st.session_state:
        st.session_state[source_review_key] = None


def ensure_chat_defaults() -> None:
    if "chat_provider" not in st.session_state:
        default_provider = "Anthropic"
        if not load_env_value(CHAT_PROVIDERS["Anthropic"]["api_key_env"])[0] and load_env_value(CHAT_PROVIDERS["OpenAI"]["api_key_env"])[0]:
            default_provider = "OpenAI"
        st.session_state["chat_provider"] = default_provider

    for provider_name, config in CHAT_PROVIDERS.items():
        session_key = config["api_key_session"]
        if not st.session_state.get(session_key):
            value, _ = load_env_value(config["api_key_env"])
            if value:
                st.session_state[session_key] = value

    provider_models = CHAT_PROVIDERS[st.session_state["chat_provider"]]["models"]
    if st.session_state.get("chat_model") not in provider_models:
        st.session_state["chat_model"] = provider_models[0]


def get_current_state(instance_id: str) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    table_state = st.session_state[f"table_state::{instance_id}"]
    draft_state = st.session_state[f"draft_state::{instance_id}"]
    comments_state = st.session_state[f"comments_state::{instance_id}"]
    curation_state = st.session_state[f"curation_state::{instance_id}"]
    plain_df = strip_ui_columns(table_state)
    return plain_df, draft_state, comments_state, curation_state


def autosave_session_state(instance_id: str) -> None:
    table_key = f"table_state::{instance_id}"
    draft_key = f"draft_state::{instance_id}"
    comments_key = f"comments_state::{instance_id}"
    curation_key = f"curation_state::{instance_id}"
    if not all(key in st.session_state for key in [table_key, draft_key, comments_key, curation_key]):
        return
    df, draft, comments, curation = get_current_state(instance_id)
    save_staged_instance(instance_id, df, draft, comments, curation)


def append_chat_event(instance_id: str, event: Dict[str, Any]) -> None:
    comments_key = f"comments_state::{instance_id}"
    comments_state = copy.deepcopy(st.session_state[comments_key])
    comments_state.setdefault("chat_events", []).append(event)
    if event.get("summary"):
        comments_state["chat_summary"] = event["summary"]
    st.session_state[comments_key] = comments_state


def set_chat_history(instance_id: str, history: List[Dict[str, str]]) -> None:
    chat_key = f"chat_history::{instance_id}"
    comments_key = f"comments_state::{instance_id}"
    st.session_state[chat_key] = copy.deepcopy(history)
    comments_state = copy.deepcopy(st.session_state[comments_key])
    comments_state["chat_history"] = copy.deepcopy(history)
    st.session_state[comments_key] = comments_state


def reload_instance_from_staging(instance_id: str) -> None:
    df_staged, draft_staged, comments_staged, curation_staged = load_staged_instance(instance_id)
    st.session_state[f"table_state::{instance_id}"] = df_staged.copy()
    st.session_state[f"comments_state::{instance_id}"] = copy.deepcopy(comments_staged)
    st.session_state[f"draft_state::{instance_id}"] = copy.deepcopy(draft_staged)
    st.session_state[f"curation_state::{instance_id}"] = copy.deepcopy(curation_staged)
    st.session_state[f"selected_rows::{instance_id}"] = []
    st.session_state[f"chat_history::{instance_id}"] = copy.deepcopy(comments_staged.get("chat_history", []))
    st.session_state[f"proposal::{instance_id}"] = None
    st.session_state[f"source_review::{instance_id}"] = None


def clear_chat(instance_id: str) -> None:
    set_chat_history(instance_id, [])
    comments_key = f"comments_state::{instance_id}"
    comments_state = copy.deepcopy(st.session_state[comments_key])
    comments_state["chat_summary"] = ""
    st.session_state[comments_key] = comments_state
    st.session_state[f"proposal::{instance_id}"] = None
    st.session_state[f"clear_chat_message::{instance_id}"] = True


def queue_chat_toast(instance_id: str, message: str) -> None:
    st.session_state[f"chat_toast::{instance_id}"] = message


def refresh_source_review(instance_id: str, source_urls: List[str]) -> Dict[str, Any]:
    payload = build_source_context_payload(source_urls)
    st.session_state[f"source_review::{instance_id}"] = payload
    return payload


def sync_selected_prompt_text(instance_id: str) -> None:
    selected_prompt_name = st.session_state.get(f"prompt_library::{instance_id}")
    if selected_prompt_name in CHAT_PROMPT_LIBRARY:
        st.session_state[f"prompt_library_text::{instance_id}"] = CHAT_PROMPT_LIBRARY[selected_prompt_name]


def sync_selected_bundle_text(instance_id: str) -> None:
    selected_bundle_name = st.session_state.get(f"bundle_library::{instance_id}")
    if selected_bundle_name in CHAT_PROMPT_BUNDLES:
        st.session_state[f"bundle_library_text::{instance_id}"] = CHAT_PROMPT_BUNDLES[selected_bundle_name]


def process_pending_chat_message_clear(instance_id: str) -> None:
    clear_key = f"clear_chat_message::{instance_id}"
    message_key = f"chat_message::{instance_id}"
    if st.session_state.get(clear_key):
        st.session_state[message_key] = ""
        st.session_state[clear_key] = False


def process_pending_chat_toast(instance_id: str) -> None:
    toast_key = f"chat_toast::{instance_id}"
    message = st.session_state.get(toast_key, "")
    if message:
        st.toast(message, icon="💬")
        st.session_state[toast_key] = ""


def get_selected_rows(instance_id: str) -> List[int]:
    return copy.deepcopy(st.session_state.get(f"selected_rows::{instance_id}", []))


def set_selected_rows(instance_id: str, rows: List[int]) -> None:
    st.session_state[f"selected_rows::{instance_id}"] = sorted(set(int(r) for r in rows))


def latest_chat_exchange(history: List[Dict[str, str]]) -> Tuple[str, str]:
    last_assistant_idx = None
    for idx in range(len(history) - 1, -1, -1):
        if history[idx].get("role") == "assistant":
            last_assistant_idx = idx
            break
    if last_assistant_idx is None:
        return "", ""

    assistant_text = history[last_assistant_idx].get("content", "")
    user_text = ""
    for idx in range(last_assistant_idx - 1, -1, -1):
        if history[idx].get("role") == "user":
            user_text = history[idx].get("content", "")
            break
    return user_text, assistant_text


def format_latest_exchange_for_agentic(instance_id: str, user_text: str, assistant_text: str) -> str:
    return (
        f"Please review this latest curation UI exchange for staged instance `{instance_id}`.\n\n"
        f"User prompt:\n{user_text}\n\n"
        f"Assistant response:\n{assistant_text}\n"
    )


def render_copy_exchange_widget(ctx, instance_id: str, payload: str) -> None:
    button_id = f"copy-latest-{instance_id}"
    with ctx:
        components.html(
            f"""
<div style="display:flex;align-items:center;margin:0.15rem 0 0.35rem 0;">
  <button id="{button_id}" type="button" style="height:2.75rem;padding:0 0.9rem;border:1px solid #d0d7de;border-radius:0.5rem;background:white;cursor:pointer;font-size:1rem;white-space:nowrap;">
    Copy latest prompt + response
  </button>
</div>
<script>
const payload = {json.dumps(payload)};
const button = document.getElementById({json.dumps(button_id)});
const defaultLabel = "Copy latest prompt + response";
let resetHandle = null;

function setButtonLabel(label) {{
  button.textContent = label;
}}

button.addEventListener("click", async () => {{
  try {{
    await navigator.clipboard.writeText(payload);
    setButtonLabel("Copied to clipboard.");
  }} catch (err) {{
    setButtonLabel("Copy failed. Use preview below.");
  }}
  if (resetHandle) {{
    window.clearTimeout(resetHandle);
  }}
  resetHandle = window.setTimeout(() => {{
    setButtonLabel(defaultLabel);
    resetHandle = null;
  }}, 1800);
}});
</script>
""",
            height=52,
        )


def render_source_review_panel(instance_id: str, source_urls: List[str], ctx) -> None:
    source_review_key = f"source_review::{instance_id}"
    payload = st.session_state.get(source_review_key)

    with ctx.expander("Source review", expanded=False):
        if st.button("Refresh source review", key=f"refresh_source_review::{instance_id}"):
            with st.spinner("Fetching source pages..."):
                payload = refresh_source_review(instance_id, source_urls)

        payload = st.session_state.get(source_review_key)
        if not source_urls:
            st.info("No source URLs in the current draft.")
            return
        if not payload:
            st.caption("No source review cached yet. Click `Refresh source review` to fetch URLs and inspect prompt coverage.")
            return

        summary_cols = st.columns(4)
        summary_cols[0].metric("URLs", int(payload.get("source_count", 0)))
        summary_cols[1].metric("Included", int(payload.get("included_count", 0)))
        summary_cols[2].metric("Budget-skipped", int(payload.get("budget_skipped_count", 0)))
        summary_cols[3].metric("Fetch errors", int(payload.get("error_count", 0)))

        rows = []
        for item in payload.get("sources", []):
            rows.append(
                {
                    "Included": "yes" if item.get("included_in_prompt") else "no",
                    "Status": item.get("status", ""),
                    "URL": item.get("url", ""),
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        for idx, item in enumerate(payload.get("sources", []), start=1):
            label = f"{idx}. {item.get('status', '')} — {item.get('url', '')}"
            with st.expander(label, expanded=False):
                if item.get("error"):
                    st.error(item["error"])
                excerpt = item.get("excerpt", "")
                if excerpt:
                    st.text_area(
                        "Excerpt preview",
                        value=excerpt,
                        height=180,
                        disabled=True,
                        key=f"source_excerpt::{instance_id}::{idx}",
                    )

        st.caption("Prompt coverage uses a bounded source-context budget so large multi-source instances remain usable.")


def call_anthropic(api_key: str, model: str, system_prompt: str, history: List[Dict[str, str]], user_message: str) -> str:
    try:
        from anthropic import Anthropic
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError("anthropic package is not installed.") from exc

    client = Anthropic(api_key=api_key, timeout=CHAT_REQUEST_TIMEOUT_SECONDS)
    messages = history + [{"role": "user", "content": user_message}]
    response = client.messages.create(
        model=model,
        max_tokens=3000,
        system=system_prompt,
        messages=messages,
    )
    text_parts = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    return "\n".join(text_parts).strip()


def call_openai(api_key: str, model: str, system_prompt: str, history: List[Dict[str, str]], user_message: str) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError("openai package is not installed.") from exc

    client = OpenAI(api_key=api_key, timeout=CHAT_REQUEST_TIMEOUT_SECONDS)
    messages = [{"role": "system", "content": system_prompt}] + history + [{"role": "user", "content": user_message}]
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_completion_tokens=3000,
    )
    content = response.choices[0].message.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts = [part.text for part in content if getattr(part, "type", None) == "text"]
        return "\n".join(text_parts).strip()
    return str(content).strip()


def render_sidebar(instance_ids: List[str], categories: List[str]) -> Optional[str]:
    st.sidebar.header(APP_TITLE)
    st.sidebar.selectbox("Provider", options=list(CHAT_PROVIDERS.keys()), key="chat_provider")
    provider = st.session_state["chat_provider"]
    config = CHAT_PROVIDERS[provider]
    api_key_value, api_key_source = load_env_value(config["api_key_env"])
    session_key = config["api_key_session"]
    if api_key_value and not st.session_state.get(session_key):
        st.session_state[session_key] = api_key_value
    st.sidebar.text_input(f"{provider} API key", type="password", key=session_key)
    if api_key_source == "environment":
        st.sidebar.caption(f"Default loaded from `${config['api_key_env']}`.")
    elif api_key_source:
        st.sidebar.caption(f"Default loaded from `{api_key_source}` via `${config['api_key_env']}`.")
    st.sidebar.selectbox(
        "Model",
        options=config["models"],
        key="chat_model",
    )
    st.sidebar.caption(f"Chat requests time out after {CHAT_REQUEST_TIMEOUT_SECONDS} seconds.")
    st.sidebar.checkbox("Fetch source page text for audits", value=True, key="fetch_source_context")

    if not instance_ids:
        st.sidebar.info("No staged instances found in datasets/pkdb_bench/staging/.")
        return None

    default_index = 0
    selected = st.sidebar.selectbox("Staged instance", options=instance_ids, index=default_index, key="selected_instance")
    st.sidebar.caption(f"{len(instance_ids)} pending staged instance(s)")
    return selected


def render_cfg_form(instance_id: str, categories: List[str]) -> None:
    draft_key = f"draft_state::{instance_id}"
    curation_key = f"curation_state::{instance_id}"
    draft = st.session_state[draft_key]
    curation = st.session_state[curation_key]

    st.sidebar.subheader("cfg.json form")
    draft["id"] = st.sidebar.text_input("id", value=draft.get("id", instance_id), disabled=True)
    draft["table_title"] = st.sidebar.text_area("table_title", value=draft.get("table_title", ""), height=80)
    draft["query_category"] = st.sidebar.selectbox(
        "query_category",
        options=categories,
        index=max(categories.index(draft.get("query_category")) if draft.get("query_category") in categories else 0, 0),
    )
    secondary_options = [""] + categories
    current_secondary = draft.get("secondary_category") or ""
    draft["secondary_category"] = st.sidebar.selectbox(
        "secondary_category",
        options=secondary_options,
        index=secondary_options.index(current_secondary) if current_secondary in secondary_options else 0,
        format_func=lambda v: "— none —" if not v else v,
    )
    draft["db_analog"] = st.sidebar.text_area("db_analog", value=draft.get("db_analog", ""), height=80)
    draft["operation_type"] = st.sidebar.selectbox(
        "operation_type",
        options=OPERATION_TYPES,
        index=OPERATION_TYPES.index(draft.get("operation_type")) if draft.get("operation_type") in OPERATION_TYPES else 0,
    )
    draft["source_count"] = int(st.sidebar.number_input("source_count", min_value=1, value=int(draft.get("source_count", 1))))
    draft["domain"] = st.sidebar.text_input("domain", value=draft.get("domain", ""))
    draft["temporal_sensitivity"] = st.sidebar.selectbox(
        "temporal_sensitivity",
        options=TEMPORAL_SENSITIVITY,
        index=TEMPORAL_SENSITIVITY.index(draft.get("temporal_sensitivity")) if draft.get("temporal_sensitivity") in TEMPORAL_SENSITIVITY else 0,
    )
    draft["gt_construction"] = st.sidebar.text_input("gt_construction", value=draft.get("gt_construction", ""))
    draft["temporal_note"] = st.sidebar.text_area("temporal_note", value=draft.get("temporal_note", ""), height=100)

    source_urls = draft.get("source_urls", []) or [""]
    updated_urls = []
    for i, url in enumerate(source_urls):
        updated_urls.append(st.sidebar.text_input(f"source_url_{i + 1}", value=url, key=f"source_url::{instance_id}::{i}"))
    if st.sidebar.button("+ add URL", key=f"add_url::{instance_id}"):
        updated_urls.append("")
        st.session_state[draft_key]["source_urls"] = updated_urls
        st.rerun()
    draft["source_urls"] = [url.strip() for url in updated_urls if url.strip()]

    extraction_notes = draft.get("_extraction_notes", "")
    if extraction_notes:
        st.sidebar.caption("Extraction notes (read-only)")
        st.sidebar.code(extraction_notes)

    with st.sidebar.expander("Curation record", expanded=False):
        st.caption("Working GT collection notes. This is not written into final cfg.json, but it is preserved in staging and review logs.")
        curation["collection_summary"] = st.text_area(
            "collection_summary",
            value=curation.get("collection_summary", ""),
            height=90,
            key=f"curation_collection_summary::{instance_id}",
        )
        curation["source_coverage_notes"] = st.text_area(
            "source_coverage_notes",
            value=curation.get("source_coverage_notes", ""),
            height=110,
            key=f"curation_source_coverage::{instance_id}",
        )
        curation["decision_log"] = st.text_area(
            "decision_log",
            value=curation.get("decision_log", ""),
            height=130,
            key=f"curation_decision_log::{instance_id}",
        )
        curation["normalization_decisions"] = st.text_area(
            "normalization_decisions",
            value=curation.get("normalization_decisions", ""),
            height=110,
            key=f"curation_normalization::{instance_id}",
        )
        curation["open_questions"] = st.text_area(
            "open_questions",
            value=curation.get("open_questions", ""),
            height=100,
            key=f"curation_open_questions::{instance_id}",
        )

    render_source_review_panel(instance_id, draft.get("source_urls", []), st.sidebar)

    st.session_state[draft_key] = draft
    st.session_state[curation_key] = curation


def render_editor(instance_id: str) -> None:
    table_key = f"table_state::{instance_id}"
    comments_key = f"comments_state::{instance_id}"
    draft_key = f"draft_state::{instance_id}"

    table_state = st.session_state[table_key]
    comments_state = st.session_state[comments_key]
    draft_state = st.session_state[draft_key]
    selected_rows_state = get_selected_rows(instance_id)

    header_cols = st.columns([2, 2, 3, 2])
    header_cols[0].metric("Doubts", len(comments_state.get("doubts", [])))
    header_cols[1].metric("Comments", len(comments_state.get("comments", [])))
    filter_text = header_cols[2].text_input("Filter rows", key=f"filter::{instance_id}")

    with header_cols[3]:
        st.write("")
        st.write("")
        if st.button("Reset to staged", key=f"reset::{instance_id}"):
            reload_instance_from_staging(instance_id)
            st.rerun()

    if filter_text:
        preview_df = build_row_flags(table_state, comments_state, selected_rows=selected_rows_state)
        mask = preview_df.astype(str).apply(lambda row: row.str.contains(filter_text, case=False, na=False)).any(axis=1)
        preview_df = preview_df[mask]
        st.caption("Filtered preview (editing always applies to the full table below).")
        st.dataframe(preview_df, use_container_width=True, hide_index=False)

    def style_amber_doubts(row):
        return ['background-color: rgba(255, 191, 0, 0.2)' if row.get(DOUBT_COL, False) else '' for _ in row]

    display_table = build_row_flags(table_state, comments_state, selected_rows=selected_rows_state)
    styled_table = display_table.style.apply(style_amber_doubts, axis=1)

    st.caption(
        "Table controls: "
        f"`{SELECT_COL}` marks rows for bulk actions, `{DOUBT_COL}` marks rows with unresolved doubt, and `{COMMENT_COUNT_COL}` shows row-linked comments. "
        "Native right-click cell comments are not available in Streamlit, so use the comment tools below for row/column/cell comments."
    )

    editor_df = st.data_editor(
        styled_table,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=False,
        disabled=[COMMENT_COUNT_COL],
        key=f"editor::{instance_id}",
    )

    selected_rows = [row_idx for row_idx in range(len(editor_df)) if bool(editor_df.iloc[row_idx].get(SELECT_COL, False))]
    rebuilt_df = strip_ui_columns(editor_df)
    rebuilt_comments = sync_doubts_from_editor(comments_state, strip_ui_columns(table_state), editor_df)
    set_selected_rows(instance_id, selected_rows)
    st.session_state[table_key] = rebuilt_df
    st.session_state[comments_key] = rebuilt_comments

    plain_df = st.session_state[table_key].copy()
    if not plain_df.empty:
        st.markdown("**Quick comment for current selection**")
        st.caption(
            "Use row checkboxes plus selected columns below, then apply a cell comment directly. "
            "The browser's drag-highlighted red area is not exposed by Streamlit, so this is the direct supported equivalent."
        )
        selected_columns = st.multiselect(
            "Selected columns",
            options=list(plain_df.columns),
            default=[],
            key=f"selected_comment_columns::{instance_id}",
            help="Together with selected rows, this defines the current rectangular cell target.",
        )
        quick_comment_text = st.text_area(
            "Comment for selected cells",
            height=90,
            key=f"quick_selection_comment::{instance_id}",
        )
        quick_cols = st.columns([2, 2, 3])
        quick_cols[0].metric("Selected rows", len(selected_rows))
        quick_cols[1].metric("Selected columns", len(selected_columns))
        if quick_cols[2].button("Add comment to selected cells", key=f"apply_quick_selected_comment::{instance_id}"):
            if not selected_rows or not selected_columns:
                st.warning("Select at least one row and one column first.")
            else:
                st.session_state[comments_key] = upsert_comment_many(
                    st.session_state[comments_key],
                    "cell",
                    selected_rows,
                    selected_columns,
                    quick_comment_text,
                )
                st.rerun()

    with st.expander("Comment tools", expanded=False):
        if plain_df.empty:
            st.info("No rows available.")
        else:
            st.caption(f"Selected rows via first column: {len(selected_rows)}")
            single_tab, bulk_tab = st.tabs(["Single comment", "Bulk comment"])

            with single_tab:
                comment_scope_choice = st.radio(
                    "Comment scope",
                    options=["cell", "row", "column"],
                    horizontal=True,
                    key=f"single_comment_scope::{instance_id}",
                )
                row_idx = None
                column = ""
                if comment_scope_choice in {"cell", "row"}:
                    row_idx = int(
                        st.number_input(
                            "Row index",
                            min_value=0,
                            max_value=max(len(plain_df) - 1, 0),
                            value=selected_rows[0] if selected_rows else 0,
                            step=1,
                            key=f"comment_row::{instance_id}",
                        )
                    )
                if comment_scope_choice in {"cell", "column"}:
                    default_col = selected_columns[0] if selected_columns else plain_df.columns[0]
                    column = st.selectbox("Column", options=list(plain_df.columns), index=list(plain_df.columns).index(default_col), key=f"comment_col::{instance_id}")
                preview = get_comment_preview(st.session_state[comments_key], comment_scope_choice, row_idx, column)
                text = st.text_area("Comment", value=preview, height=160, key=f"comment_text::{instance_id}")
                c1, c2 = st.columns(2)
                if c1.button("Save comment", key=f"save_comment::{instance_id}"):
                    st.session_state[comments_key] = upsert_comment(st.session_state[comments_key], comment_scope_choice, row_idx, column, text)
                    st.rerun()
                if c2.button("Delete comment", key=f"delete_comment::{instance_id}"):
                    st.session_state[comments_key] = upsert_comment(st.session_state[comments_key], comment_scope_choice, row_idx, column, "")
                    st.rerun()

            with bulk_tab:
                scope = st.radio(
                    "Bulk comment type",
                    options=[
                        "Cell comments on selected rows × selected columns",
                        "Row comments on selected rows",
                        "Column comments on selected columns",
                    ],
                    key=f"bulk_comment_scope::{instance_id}",
                )
                bulk_text = st.text_area(
                    "Bulk comment text",
                    height=140,
                    key=f"bulk_comment_text::{instance_id}",
                )
                if scope == "Cell comments on selected rows × selected columns":
                    target_rows = selected_rows
                    target_columns = selected_columns
                    target_scope = "cell"
                elif scope == "Row comments on selected rows":
                    target_rows = selected_rows
                    target_columns = []
                    target_scope = "row"
                else:
                    target_rows = []
                    target_columns = selected_columns
                    target_scope = "column"

                st.caption(f"Bulk target size: {len(target_rows)} row(s) × {len(target_columns)} column(s)")
                c3, c4 = st.columns(2)
                if c3.button("Apply bulk comment", key=f"apply_bulk_comment::{instance_id}"):
                    if target_scope == "cell" and (not target_rows or not target_columns):
                        st.warning("Select at least one target row and one target column.")
                    elif target_scope == "row" and not target_rows:
                        st.warning("Select at least one target row.")
                    elif target_scope == "column" and not target_columns:
                        st.warning("Select at least one target column.")
                    else:
                        st.session_state[comments_key] = upsert_comment_many(st.session_state[comments_key], target_scope, target_rows, target_columns, bulk_text)
                        st.rerun()
                if c4.button("Clear bulk comment", key=f"clear_bulk_comment::{instance_id}"):
                    if target_scope == "cell" and (not target_rows or not target_columns):
                        st.warning("Select at least one target row and one target column.")
                    elif target_scope == "row" and not target_rows:
                        st.warning("Select at least one target row.")
                    elif target_scope == "column" and not target_columns:
                        st.warning("Select at least one target column.")
                    else:
                        st.session_state[comments_key] = upsert_comment_many(st.session_state[comments_key], target_scope, target_rows, target_columns, "")
                        st.rerun()

    with st.expander("Comments manager", expanded=True):
        st.caption("All comments")
        comments_df = comments_dataframe(st.session_state[comments_key])
        if comments_df.empty:
            st.info("No comments yet.")
        else:
            st.dataframe(comments_df, use_container_width=True, hide_index=True)
            manager_cols = st.columns(2)
            edit_id = manager_cols[0].selectbox(
                "Edit comment id",
                options=comments_df["comment_id"].tolist(),
                key=f"edit_comment_id::{instance_id}",
            )
            current_row = comments_df[comments_df["comment_id"] == edit_id].iloc[0]
            manager_cols[1].caption(
                f"Editing {current_row['scope']} comment"
                f"{'' if pd.isna(current_row['row']) else f' on row {int(current_row['row'])}'}"
                f"{'' if not current_row['column'] else f', column {current_row['column']}'}"
            )
            edit_text = st.text_area(
                "Edit comment text",
                value=str(current_row["text"]),
                height=120,
                key=f"edit_comment_text::{instance_id}",
            )
            edit_cols = st.columns(3)
            if edit_cols[0].button("Update selected comment", key=f"update_selected_comment::{instance_id}"):
                st.session_state[comments_key] = update_comment_by_index(st.session_state[comments_key], int(edit_id), edit_text)
                st.rerun()
            if edit_cols[1].button("Delete selected comment", key=f"delete_one_comment::{instance_id}"):
                st.session_state[comments_key] = remove_comments_by_indices(st.session_state[comments_key], [int(edit_id)])
                st.rerun()
            delete_ids = st.multiselect(
                "Delete multiple comment ids",
                options=comments_df["comment_id"].tolist(),
                key=f"delete_comment_ids::{instance_id}",
            )
            c5, c6 = st.columns(2)
            if c5.button("Delete selected comments", key=f"delete_selected_comments::{instance_id}"):
                st.session_state[comments_key] = remove_comments_by_indices(st.session_state[comments_key], delete_ids)
                st.rerun()
            if c6.button("Clear all comments", key=f"clear_all_comments::{instance_id}"):
                updated_comments = copy.deepcopy(st.session_state[comments_key])
                updated_comments["comments"] = []
                st.session_state[comments_key] = updated_comments
                st.rerun()

    current_df = strip_ui_columns(st.session_state[table_key])
    cfg = load_cfg()
    errors, warnings = validate_state(instance_id, current_df, draft_state, st.session_state[comments_key], cfg)
    footer = st.container()
    with footer:
        if errors:
            st.error("Errors: " + "; ".join(errors))
        else:
            st.success("No blocking errors.")
        if warnings:
            st.warning("Warnings: " + "; ".join(warnings))


def render_chatbot(instance_id: str, ctx) -> None:
    chat_key = f"chat_history::{instance_id}"
    proposal_key = f"proposal::{instance_id}"
    process_pending_chat_message_clear(instance_id)
    process_pending_chat_toast(instance_id)
    history = st.session_state[chat_key]
    draft = st.session_state[f"draft_state::{instance_id}"]
    comments = st.session_state[f"comments_state::{instance_id}"]
    curation = st.session_state[f"curation_state::{instance_id}"]
    df = strip_ui_columns(st.session_state[f"table_state::{instance_id}"])

    panel = ctx.container(height=980, border=True)
    panel.subheader("Chat assistant")
    panel.caption(
        "Receives the full CSV, source URLs, benchmark registration draft, schema, and open doubts. "
        "This panel scrolls independently from the Review/Coverage area. Source review is in the left sidebar."
    )

    latest_user_text, latest_assistant_text = latest_chat_exchange(history)

    utility_cols = panel.columns([2, 1])
    handoff_payload = ""
    if latest_assistant_text:
        handoff_payload = format_latest_exchange_for_agentic(instance_id, latest_user_text, latest_assistant_text)
        if utility_cols[0].button("Copy latest prompt + response", key=f"show_handoff::{instance_id}"):
            st.session_state[f"show_handoff_preview::{instance_id}"] = not st.session_state.get(f"show_handoff_preview::{instance_id}", False)
    else:
        utility_cols[0].caption("No assistant response yet to copy.")
    if utility_cols[1].button("Clear chat", key=f"clear_chat::{instance_id}"):
        clear_chat(instance_id)
        st.rerun()

    if st.session_state.get(f"show_handoff_preview::{instance_id}") and handoff_payload:
        panel.code(handoff_payload, language="text")

    history_box = panel.container(height=340)
    if history:
        for message in history:
            speaker = "User" if message["role"] == "user" else "Assistant"
            history_box.markdown(f"**{speaker}:** {message['content']}")
    else:
        history_box.info("No chat messages yet.")
    if latest_assistant_text:
        with panel.expander("Handoff preview", expanded=False):
            st.text_area(
                "Latest prompt + response",
                value=handoff_payload,
                height=220,
                key=f"handoff_preview::{instance_id}",
            )

    with panel.expander("Prompt library", expanded=False):
        prompt_names = list(CHAT_PROMPT_LIBRARY.keys())
        selected_prompt_name = st.selectbox(
            "Template",
            options=prompt_names,
            key=f"prompt_library::{instance_id}",
            on_change=sync_selected_prompt_text,
            args=(instance_id,),
        )
        prompt_guidance = CHAT_PROMPT_GUIDANCE.get(selected_prompt_name)
        if prompt_guidance:
            st.info(prompt_guidance)
        selected_prompt = CHAT_PROMPT_LIBRARY[selected_prompt_name]
        st.text_area(
            "Prompt text (editable)",
            value=st.session_state.get(f"prompt_library_text::{instance_id}", selected_prompt),
            height=120,
            key=f"prompt_library_text::{instance_id}",
        )
        if st.button("Load into message box", key=f"use_prompt::{instance_id}"):
            st.session_state[f"chat_message::{instance_id}"] = st.session_state[f"prompt_library_text::{instance_id}"]
            st.rerun()

    with panel.expander("Audit bundles", expanded=True):
        st.caption("Recommended for normal review. Bundles combine related checks so you do not need to click templates one by one.")
        bundle_names = list(CHAT_PROMPT_BUNDLES.keys())
        selected_bundle_name = st.selectbox(
            "Bundle",
            options=bundle_names,
            key=f"bundle_library::{instance_id}",
            on_change=sync_selected_bundle_text,
            args=(instance_id,),
        )
        st.info(CHAT_BUNDLE_GUIDANCE.get(selected_bundle_name, ""))
        selected_bundle = CHAT_PROMPT_BUNDLES[selected_bundle_name]
        st.text_area(
            "Bundle prompt (editable)",
            value=st.session_state.get(f"bundle_library_text::{instance_id}", selected_bundle),
            height=180,
            key=f"bundle_library_text::{instance_id}",
        )
        if st.button("Load bundle into message box", key=f"use_bundle::{instance_id}"):
            st.session_state[f"chat_message::{instance_id}"] = st.session_state[f"bundle_library_text::{instance_id}"]
            st.rerun()

    user_message = ctx.text_area(
        "Message",
        key=f"chat_message::{instance_id}",
        height=140,
        placeholder="Type a message for the curation assistant",
    )
    send_row = ctx.columns([1, 2])
    send_chat = send_row[0].button("Send message", key=f"send_chat::{instance_id}")
    send_status = send_row[1].empty()
    if send_chat and user_message.strip():
        user_message = user_message.strip()
        provider = st.session_state.get("chat_provider", "Anthropic")
        provider_config = CHAT_PROVIDERS[provider]
        api_key = st.session_state.get(provider_config["api_key_session"], "")
        model = st.session_state.get("chat_model", "claude-sonnet-4-6")
        fetch_source_context_enabled = st.session_state.get("fetch_source_context", True)
        if not api_key:
            ctx.error(f"Enter a {provider} API key to use the chatbot.")
        else:
            try:
                with send_status, st.spinner("Asking the curation assistant..."):
                    source_context = ""
                    source_errors: List[str] = []
                    if fetch_source_context_enabled and draft.get("source_urls"):
                        source_context, source_errors, source_payload = build_source_context(draft.get("source_urls", []))
                        st.session_state[f"source_review::{instance_id}"] = source_payload
                    system_prompt = build_system_prompt(instance_id, draft, df, comments, curation, source_context=source_context)
                    if provider == "OpenAI":
                        assistant_text = call_openai(api_key, model, system_prompt, history, user_message)
                    else:
                        assistant_text = call_anthropic(api_key, model, system_prompt, history, user_message)
            except Exception as exc:  # pragma: no cover - depends on runtime config
                ctx.error(
                    f"Chat request failed: {exc}. "
                    f"Check that the {provider} API key is valid, the selected model is enabled for your account, and outbound network access is available."
                )
            else:
                history = history + [{"role": "user", "content": user_message}, {"role": "assistant", "content": assistant_text}]
                set_chat_history(instance_id, history)
                st.session_state[f"clear_chat_message::{instance_id}"] = True
                queue_chat_toast(instance_id, f"{provider} response received. Review the assistant panel for details.")
                if fetch_source_context_enabled and source_errors:
                    ctx.warning("Some source pages could not be fetched: " + "; ".join(source_errors[:3]))
                append_chat_event(
                    instance_id,
                    {
                        "at": now_iso(),
                        "action": "assistant_response",
                        "summary": assistant_text[:200],
                        "source_cited": parse_tagged_block(assistant_text, "<!--source_citation-->", "<!--end_source_citation-->") or "",
                        "source_fetch_enabled": fetch_source_context_enabled,
                    },
                )

                proposed_df, proposal_error = parse_table_update_csv(assistant_text)
                citation, citation_error = parse_source_citation(assistant_text)
                if proposed_df is not None:
                    proposal = {
                        "assistant_text": assistant_text,
                        "table_df": proposed_df,
                        "table_error": proposal_error,
                        "citation": citation,
                        "citation_error": citation_error,
                        "created_at": time.time(),
                        "review_ready": False,
                    }
                    st.session_state[proposal_key] = proposal
                st.rerun()
    elif send_chat:
        ctx.warning("Enter a message first.")

    proposal = st.session_state.get(proposal_key)
    if proposal:
        panel.markdown("---")
        panel.caption("Proposed table update")
        if not proposal.get("review_ready"):
            proposal["review_ready"] = True
            st.session_state[proposal_key] = proposal
            st.rerun()

        current_df = strip_ui_columns(st.session_state[f"table_state::{instance_id}"])
        proposed_df = proposal["table_df"]
        diffs, summary = compute_diff(current_df, proposed_df, primary_key=draft.get("primary_key", []))
        panel.code(
            f'Proposed change — {summary["rows_changed"]} rows changed · '
            f'{summary["rows_added"]} rows added · {summary["rows_deleted"]} rows deleted'
        )
        if diffs:
            diff_preview = pd.DataFrame(diffs[:50])
            panel.dataframe(diff_preview, use_container_width=True, hide_index=True)
        else:
            panel.info("No differences detected.")

        apply_errors = []
        if proposal.get("table_error"):
            apply_errors.append(proposal["table_error"])
        if proposal.get("citation_error"):
            apply_errors.append(proposal["citation_error"])
        if proposal.get("citation") is not None:
            coverage_error = validate_evidence_coverage(diffs, proposal["citation"])
            if coverage_error:
                apply_errors.append(coverage_error)

        if apply_errors:
            panel.error("Apply blocked: " + "; ".join(apply_errors))

        c1, c2 = panel.columns(2)
        if c1.button("Apply table update", disabled=bool(apply_errors), key=f"apply_proposal::{instance_id}"):
            st.session_state[f"table_state::{instance_id}"] = proposed_df.copy()
            st.session_state[f"selected_rows::{instance_id}"] = []
            append_chat_event(
                instance_id,
                {
                    "at": now_iso(),
                    "action": "table_update_applied",
                    "summary": f"Applied assistant proposal with {len(diffs)} diff item(s).",
                    "source_cited": json.dumps(proposal.get("citation", {})),
                    "diff_summary": summary,
                    "evidence_map": proposal.get("citation", {}).get("evidence_map", []) if proposal.get("citation") else [],
                },
            )
            st.session_state[proposal_key] = None
            st.rerun()
        if c2.button("Dismiss proposal", key=f"dismiss_proposal::{instance_id}"):
            st.session_state[proposal_key] = None
            st.rerun()


def render_actions(instance_id: str) -> None:
    df, draft, comments, curation = get_current_state(instance_id)
    cfg = load_cfg()
    errors, warnings = validate_state(instance_id, df, draft, comments, cfg)

    action_cols = st.columns(4)
    if action_cols[0].button("Reject staging", key=f"reject::{instance_id}"):
        delete_staged_instance(instance_id)
        keys_to_clear = [k for k in list(st.session_state.keys()) if instance_id in str(k)]
        for key in keys_to_clear:
            del st.session_state[key]
        st.success(f"Deleted staging files for {instance_id}.")
        st.rerun()

    if action_cols[1].button("Reload staged", key=f"reload_action::{instance_id}"):
        reload_instance_from_staging(instance_id)
        st.info("Reloaded staged files.")
        st.rerun()

    if action_cols[2].button("Save draft", key=f"save::{instance_id}"):
        save_staged_instance(instance_id, df, draft, comments, curation)
        st.success("Saved staged draft.")

    reviewed_warnings = True
    if warnings:
        st.info("Warnings must be acknowledged before registration.")
        reviewed_warnings = st.checkbox(
            "I reviewed the warnings and want to proceed if there are no blocking errors.",
            key=f"review_warnings::{instance_id}",
        )

    register_disabled = bool(errors) or (bool(warnings) and not reviewed_warnings)
    if action_cols[3].button("Register to cfg.json", disabled=register_disabled, key=f"register::{instance_id}"):
        register_instance(instance_id, df, draft, comments, curation)
        st.success(f"Registered {instance_id}.")
        keys_to_clear = [k for k in list(st.session_state.keys()) if instance_id in str(k)]
        for key in keys_to_clear:
            del st.session_state[key]
        st.rerun()


def render_coverage_tab(cfg: Dict[str, Dict[str, Any]], staged_ids: List[str]) -> None:
    st.subheader("Coverage")
    coverage_df = compute_category_coverage(cfg)
    categories_covered = int((coverage_df["Registered"] > 0).sum()) if not coverage_df.empty else 0
    st.caption(
        f"Registered: {len(cfg)} / 82 · Categories covered: {categories_covered} / 15 · "
        f"Staged: {len(staged_ids)} pending"
    )
    st.dataframe(coverage_df, use_container_width=True, hide_index=True)

    if coverage_df.empty:
        return

    next_candidates = coverage_df.sort_values(["Gap", "Registered"], ascending=[False, True])
    if not next_candidates.empty:
        next_target = next_candidates.iloc[0]["Category"]
        st.info(f"Next target category: {next_target}")

    for _, row in coverage_df.iterrows():
        category = row["Category"]
        with st.expander(f"{category} ({row['Registered']} registered)"):
            registered = [
                {
                    "id": item["id"],
                    "table_title": item.get("table_title", ""),
                    "sources": ", ".join(item.get("source_urls", [])),
                }
                for item in cfg.values()
                if item.get("query_category") == category
            ]
            if registered:
                st.dataframe(pd.DataFrame(registered), use_container_width=True, hide_index=True)
            else:
                st.write("No registered instances yet.")
            prompt = SUGGESTED_PROMPTS.get(category, "Stage a new PKDB-Bench instance for this category with authoritative source grounding.")
            st.code(prompt)


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    render_layout_css()
    ensure_directories()
    ensure_chat_defaults()

    cfg = load_cfg()
    categories = parse_categories()
    staged_ids = list_staged_instance_ids()

    selected_instance = render_sidebar(staged_ids, categories)

    # Two-column layout at page level: tabs on the left, chat always on the right.
    main_col, chat_col = st.columns([3, 2])

    with main_col:
        review_tab, coverage_tab = st.tabs(["Review", "Coverage"])

        with coverage_tab:
            render_coverage_tab(cfg, staged_ids)

        with review_tab:
            if not selected_instance:
                st.info("Stage an instance first by writing staging CSV + draft JSON into datasets/pkdb_bench/staging/.")
            else:
                df, draft, comments, curation = load_staged_instance(selected_instance)
                ensure_session_defaults(selected_instance, df, draft, comments, curation)
                render_cfg_form(selected_instance, categories)
                render_editor(selected_instance)
                st.markdown("---")
                st.caption("`Save draft` persists the current table, benchmark metadata, curation record, comments, and chatbot history back to staging. `Reload staged` restores the latest staged files, including external updates. `Clear chat` resets only the current conversation and pending proposal.")
                render_actions(selected_instance)
                autosave_session_state(selected_instance)

    if selected_instance:
        render_chatbot(selected_instance, ctx=chat_col)
    else:
        chat_col.info("Select a staged instance to use the chat assistant.")


if __name__ == "__main__":
    main()
