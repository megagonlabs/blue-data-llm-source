# Centralized Prompts for PKDB Framework

# --- QUERY ANALYSIS ---

QUERY_ANALYSIS_PROMPT = """
User Query: "{query}"

Analyze this query and extract:
1. **Refined Query**: A clear, standalone natural language task description.
2. **Constraints**: A JSON dictionary of hard filters (e.g. {{"Year": "2023", "Country": "USA", "Generation": "4"}}). 
   - Extract ALL implicit filters as explicit constraints.
   - Use empty {{}} if none.
3. **Collection Strategy**: "ALL" (default), "LIMIT <N>", or "MOST_COMMON".

Return JSON:
{{
    "refined_query": "...",
    "constraints": {{...}},
    "collection_strategy": "..."
}}
"""

QUERY_REWRITE_PROMPT = """
You are an expert Data Engineer optimizing queries for an LLM-based database.

Goal: Rewrite the user query to be:
- **Precise**: Resolve ambiguities.
- **Comprehensive**: Clearly state the intent to retrieve a complete structured dataset.
- **Constraint-Aware**: Preserve all filters and constraints (e.g. "US Cities" -> "The cities' country is USA").
- **Unit-Clear**: Explicitly state what ONE ROW represents in the result table.

Guidelines:
- Clarify ambiguous terms.
- Specify whether the query wants a "complete list" / "all items" or just some (like 100).
- Mention including key attributes if implied.
- **Identify the unit of analysis**: Ask yourself — what entity does each row describe?
  - If the query asks for statistics/measurements/data FOR a set of named entities (states, countries, years, categories), add "one row per [entity]" to make the granularity explicit.
  - Example: "poverty statistics in India" → append "one row per Indian state/union territory"
  - Example: "demographics of Russia 1946–2012" → append "one row per year"
  - Example: "tournament results" → append "one row per participant/team"
  - **CRITICAL — demographic/census/vital statistics time series**: ALWAYS write "one row per year", regardless of how many years/decades the query spans. Do NOT write "one row per decade". Wikipedia stores these tables at yearly granularity. Correct: "vital statistics of Liechtenstein 1901–2011, one row per year (1901, 1902, …, 2011)."
  - If the granularity is genuinely unclear, mention both interpretations and pick the most specific.

Output Requirements:
- Return ONLY the rewritten query text.
- NO quotes, NO explanation, NO JSON.

Input Query: "{query}"
"""

ROW_ESTIMATION_PROMPT = """
Query: "{query}"

Estimate the total number of rows (items) that would be in the FULL result table for this query.
Think carefully about what ONE ROW represents before estimating.

Bounded sets — use the actual count:
- "US States" → ~50, "world countries" → ~195, "Indian states/union territories" → ~36
- If the query asks for statistics/indicators/data FOR administrative divisions of a region (states, provinces, prefectures, countries), the row count = number of those divisions — NOT the total population or number of measurements.
- A bounded time series (e.g., "year X to year Y") → number of YEARS in that range. Example: 1900–2010 = 111 rows, NOT 11 rows (decades).
- CRITICAL for multi-decade demographic/census/population tables: Wikipedia stores these year-by-year, one row per year. Even if the rewritten query says "one row per decade," estimate the number of YEARS in the span. A query covering 1900–2010 should return ~111, not ~11.

Open-ended entity lists — lean toward the higher end:
- "Pokemon" → ~1000, "Movies", "Cities", "People", or "Events" over a long time range → LARGE (100+, possibly 1000+).
- Rankings, tournament results, competition participants, championship entries, episode lists, or enumerations across multiple rounds or years → LARGE (100-500+).

Balance:
- For well-defined bounded sets, use the actual known count.
- For open-ended entity lists, lean toward the higher end.
- Do NOT conflate "comprehensive statistics" for a bounded set with a large row count.

Return ONLY a single integer number representing the expected row count.
"""

DECOMPOSITION_DECISION_PROMPT = """
Query: "{query}"
Estimated Rows: {estimated_rows}

Determine if this extraction task requires "Map-Reduce Decomposition" (breaking into partitions) or if it can be done in a "Flat" single pass.

Guidelines:
- Flat: Small lists, simple fixed sets (e.g. "The 7 Continents"), that could be fetched in a single query.
- MapReduce: Large lists, open-ended sets, or complex attributes requiring detail, that would require multiple queries.
- Also consider if there are natural partition keys (e.g. "Category", "Year", "Region").

Return JSON Code:
{{
    "requires_map_reduce": true/false,
    "reason": "Brief explanation..."
}}
"""

# --- SCHEMA GENERATION ---

SCHEMA_POOL_PROMPT = """
User Query: "{query}"

Brainstorm 15 potential attributes (columns) for a database table satisfying this query.
Include:
- Visible attributes (directly requested)
- Latent/Hidden attributes (e.g. "Release Year", "Country", "Genre", "Scientific Class", "Generation") that are valuable for filtering or partitioning.

Return JSON list of strings.
Example: ["Name", "year", "genre", "director", "studio"]
"""

SCHEMA_CANDIDATES_PROMPT = """
You are an expert database architect that designs database schemas to decompose user query by partitions.
User Query: "{query}"
Available Attribute Pool: [{pool_str}]

Propose {k} different table schemas that split user query into subqueries (partitions).
Select the best subset of attributes from the pool (and add unique IDs if needed).
For each schema, provide:
1. Table Name
2. List of Columns (Name, Description)
3. Primary Key(s)

Guidelines:
1. **Entity Centric**: The schema MUST include the main Entity Name (e.g. "City", "Pokemon Name").
2. **Scope Aware**: Respect constraints (e.g. if "US Cities", "Country" is a static attribute/constraint, maybe less useful for partitioning).
3. **Complete**: Ensure all requested data can be stored.
4. **Natural Keys Preference**: 
   - Prefer **NATURAL keys** (e.g. "ISOCode", "Username", "ScientificName") over generic integer IDs (e.g. "CountryID", "RowID") if available.
   - Integer IDs generated by LLMs in parallel partitions will COLLIDE (e.g. ID=1 in both partitions).
   - Only use an ID column if no unique natural attribute exists.

CRITICAL: 
- Do NOT produce schemas with ONLY IDs.
- Ensure clear, descriptive column names.

Output format: JSON object with key "schemas".

Output Template (JSON):
{{
    "schemas": [
        {{
            "table_name": "EntityTableName",
            "columns": [
                {{"name": "Attribute1", "description": "Description of attribute"}},
                {{"name": "Attribute2", "description": "Description of another attribute"}}
            ],
            "primary_keys": ["Attribute1"]
        }}
    ]
}}
"""

SCHEMA_RANKING_PROMPT = """
User Query: "{query}"

I have {num_candidates} candidate schemas:
{candidates_str}

Which schema is the BEST for general-purpose extraction if I want to decompose the query into subqueries (partitions)?
Rank based on:
- Natural / Robust Primary Keys: Downgrade schemas that rely on generic "ID" columns (like CountryID, RowID) unless they are official IDs (like PokedexNumber) that binded with records. We process partitions in parallel, so generic sequential IDs will duplicate (1, 2, 3...) and collide.
- the partitions under this schema are independent and can be processed in parallel
- the partitions under this schema are roughly equal in size
- Atomicity (columns are not composite)
- Orthogonality (no redundant columns)
- Coverage: Captures all necessary attributes.

BAD Schemas:
- Only Numeric stats (no Names)
- Only IDs and Foreign Keys
- Missing crucial grouping columns
- Entities satisfy more than one partition

Return ONLY the index (0-based) of the best schema.
"""

SCHEMA_TYPE_INFERENCE_PROMPT = """
Data Query: "{query}"
Table: {table_name}
Columns: {columns_str}

For each column, determine:
1. Description: A brief description of what this column represents.
2. Semantic Type (categorical, numerical, time, entity, text, etc.)
3. Data Type (int, float, date, str, bool, list, etc.)
4. Formatting: A format string that ensures uniform values across multiple LLM calls.
   Use empty string if no specific format is needed.
   Examples: "YYYY-MM-DD", "Full name (no abbreviation)", "Abbreviation only",
   "Numeric with commas (e.g. 35,552)", "Season as YYYY-YY (e.g. 2000-01)".
   Specify exact separator characters for range-style keys to prevent mismatch
   between parallel extraction calls.

Return JSON:
{{
    "columns": {{
        "ColumnName": {{"description": "...", "type": "...", "data_type": "...", "formatting": "..."}},
        ...
    }}
}}
"""

# --- ESTIMATION & STRATEGY ---

DISTRIBUTION_ESTIMATION_PROMPT = """
Your Task: Analyze the data distribution for the partition key "{key_name}" given the query "{query}".

Guidelines:
1. **Partitions**: List the most suitable partitions to divide large data into chunks that mutually exclusive and cover the entire data. Please note that partition keys are not always categorical, you might use interval (e.g. year 2003-2009, ID from 11 to 20, etc.) as partitions if it makes sense.
2. **Estimate Rows**: List the likely partitions and estimate the number of rows (entities) in each partition.
3. **Assess Ambiguity**: Is this partition key clear and mutually exclusive? (0.0=Clear, 1.0=Ambiguous).
4. **Common Sense Scope**:
   - Pay attention to the query scope and constrains. If the query implies a specific scope or constrains (e.g. "US Cities"), corresponding partition keys like "Country" could be limited (e.g. only "USA").
   - **Scope Enforcement**: If the query limits the scope, only list values within the scope of the query. Verify each value against the constraints.
     - Example: If constraints have "Year": "2020", do NOT list years like "2019" or "2021".
   - **Sub-ranging**: If the current partition is already a range (e.g. 1-100) and is too large, prefer sub-dividing this range (e.g. 1-10, 11-20, etc.) over switching to a new key, as this preserves orthogonality.
   - **Avoid Noise**: Do NOT select high-cardinality "ID-like" keys like "Zip Code", "Phone Number", "Coordinates", or "Lat/Lon" unless the query EXPLICITLY asks for them. For general demographics or political data, prefer "State", "Region", or "Year".
   - **Avoid Placeholders**: Do NOT use generic placeholders like "Player1", "Team A", "Item 1" unless they are the actual data values. If you do not know the specific entities, do not partition by this key.
   - Do NOT hallucinate values outside the query scope.
5. **Cardinality Calibration**:
   - Estimate the distinct-value count for this key within the query scope.
   - If distinct values >> expected total rows, this key is too granular.
   - Prefer range/interval partitions (e.g. group 1-20, 21-40) if it helps reduce granularity while maintaining orthogonality.
   - If you cannot enumerate a finite, known set with confidence,
     raise ambiguity_score toward 1.0 and explain in reasoning.
6. **No Continuous Metric Partitioning**: Do NOT partition by continuous numeric measurements — scores, rates, percentages, index values, float measurements, or any column that varies on a continuous scale. Such keys produce arbitrary or unbounded cardinality and meaningless partition boundaries. If the best candidate key is a continuous metric, set ambiguity_score >= 0.8 and explain in reasoning.
7. **Partition-Total Consistency**: The total estimated rows for this query is approximately **{total_rows}**.
   - For **categorical keys** (teams, countries, names, categories, etc.): List ALL known distinct values relevant to the query scope. If Σ(partition_estimates) >> {total_rows}, **reduce per-value row estimates** proportionally — do NOT drop partition values to meet the cap. Every named entity must appear as its own partition.
   - For **range/interval keys** (year ranges, rank ranges, ID ranges): Adjust the interval width or per-interval estimate so Σ stays near {total_rows}. Omitting intervals outside the query scope is acceptable.
   - Flag any large inconsistency in reasoning.
8. **No Catch-All Partitions**: Do NOT include partition values like "Other", "Miscellaneous", "Unknown", "Remaining", or any vague catch-all that cannot be specifically queried. A catch-all partition is unextractable — the LLM cannot retrieve "results for countries classified as Other". If the data has a natural "other" overflow that cannot be specifically named, raise ambiguity_score >= 0.7.

Return only JSON Code in the following format:
{{
    "partitions": {{
        "PartitionName1": <est_count>,
        "PartitionName2": <est_count>,
        ...
    }},
    "ambiguity_score": <float 0.0-1.0>,
    "reasoning": "<Explain if the query is good to be partitioned by this key, how you determined the partitions, and other relevant information>"
}}

Query: "{query}"
Potential Partition Key: "{key_name}"
Constraints: {constraints}
Total Estimated Rows: {total_rows}
"""

PARTITION_VALUES_PROMPT = """
Task: List ALL possible partition values for partition key "{key_name}" that are STRICTLY relevant to the query scope.

Data Query: "{query}"
Partition Key: "{key_name}"
Partition Key Description: "{key_description}"
Primary Key of the final table: "{primary_key}"
Constraints: {constraints}

CRITICAL RULES:
1. **Partitions**: List the most suitable partitions to divide the target data for the data query into chunks that are mutually exclusive and cover the entire data. Partition keys are not always categorical — you may also use intervals (e.g. year 2003-2009, ID from 11 to 20, etc.) when it makes sense.
2. **Relevance Check**: Do NOT output values for keys like "Zip Code" or "Phone Number" unless the query is explicitly about them. If the key seems irrelevant to the query (e.g. asking for "Zip Code" for a list of Presidents), return an empty list.
3. **Scope Enforcement**: If the query limits the scope, only list values within the scope of the query. Verify each value against the constraints.
4. **Avoid Placeholders**: Do NOT use generic placeholders like "Player1", "Team A", "Item 1" unless they are the actual data values.
5. **Completeness**: List ALL possible partition values according to factual common sense, query context, and constraints.
6. **Relevance**: Only list partition values that actually contain data for this query.
7. **Limit**: Return at most {max_partitions} partition values.
   If the natural cardinality exceeds this limit, prefer range/interval groupings
   (e.g., "Years 2000-2005", "IDs 1-50") over a flat list of individual values.
   If you cannot enumerate a finite, known set, return an empty list [].

Format: Return a valid JSON object with key "values" containing the list of strings.
Example: {{ "values": ["Value1", "Value2"] }}
"""

SUBQUERY_REWRITE_PROMPT = """
Base Query: "{base_query}"
Partition Filter: {key} = {value}
Existing Constraints: {constraints}
Collection Strategy: {strategy}

Rewrite the query to naturally include the Partition Filter.
The goal is to create a specific sub-task that retrieves ONLY the data for this partition.

Examples:
- Query: "List all Pokemon", Filter: Type = Fire -> "List all Fire-type Pokemon"
- Query: "List US Cities", Filter: State = CA -> "List all US Cities in California"
- Query: "List Nobel Winners", Filter: Field = Physics -> "List all Nobel Prize winners in Physics"

CRITICAL:
- Do NOT hallucinate new constraints.
- Do NOT repeat the base query if it contradicts the new filter.
- Ensure the result is a subset of the Base Query.

Return ONLY the rewritten query text.
"""

PARTITION_SELECTION_PROMPT = """
Data Query: "{query}"
Constraints: {constraints}

Candidates (schema columns evaluated for partitioning):
{candidates_str}

Task: Select the BEST partitioning strategy from the candidates above.

**Option A — Schema Column Partitioning (preferred)**
Select one of the schema candidates above. Use when a column cleanly divides the data
with manageable, known distinct values (categorical, temporal, or location type).

**Option B — Interval/Range Partitioning**
Use an existing schema candidate column but partition it into ordered ranges rather than
individual values (e.g., "Year 2000-2005" / "Year 2006-2010"). Use when cardinality is
too high for Option A but the data has a natural ordering.

**Option C — Latent Dimension (last resort)**
ONLY use this if ALL schema column candidates have ambiguity_score >= 0.7.
Introduce a well-known external categorization not in the schema that naturally organizes
the data (e.g., partitioning by Genre for a music table that lacks a Genre column).
Requirement: the dimension must have finite, well-known, enumerable values.

Return JSON:
{{
    "option": "A" | "B" | "C",
    "selected_key": "column name (Options A/B) or new dimension name (Option C)",
    "reasoning": "Why this partitioning strategy is best..."
}}
"""

# --- EXECUTION & EXTRACTION ---

# Note: Task prompt is constructed dynamically in DataTask.to_prompt() due to high variability
# But we can extract the template parts if needed. For now keeping dynamic.

DATA_EXTRACTION_PROMPT = """
User Request: "{query}"
Target Scope:
{constraint_str}
{estimate_str}

**CRITICAL INSTRUCTION**: According to {source}, extract ALL data matching the request.
- Extract the COMPLETE set of items. Do NOT limit yourself to "the most famous",
  "the most common", or any arbitrary top-N selection.
- Do NOT stop early because you have reached a convenient stopping point.
  Continue until you genuinely have no more entries to add.
- When in doubt, include more rows rather than fewer. Missing rows are worse than extras.

**OUTPUT FORMAT**:
Return a valid JSON Object with a single key "items" containing the list of rows.
Example:
{{
    "items": [
        {{"Column1": "Value1", ...}},
        ...
    ]
}}
DO NOT return a raw list. DO NOT return Markdown.
"""

DATA_EXTRACTION_PARTITION_CONTEXT = """
You are processing a PARTITION: "{partition_key}" = "{partition_value}".
You MUST ONLY return items where "{partition_key}" is equal to "{partition_value}".
{partition_estimate_str}"""

ROW_BY_ROW_KEYS_PROMPT = """
You are a retriever of facts.
We want to create a table with the detailed information about {query} according to {source}
{key_desc}.
List all {key_col} entities for the table.
Never shorten the list with ellipsis (...) in the middle. List every single entity found.
The response will be formatted as JSON object shown below.

RESPONSE FORMAT:
{{
    "keys": [
        {{
            "{key_col}": "value"
        }}
    ]
}}
"""

ROW_BY_ROW_FETCH_PROMPT = """
You are a retriever of facts.
We want to create a table with the detailed information about {query} according to {source}.
Columns in the table are {columns}.
{key_desc}.
Retrieve a single row whose key is {key_val}.
The response will be formatted as JSON dictionary shown below.
Pay special attention to wrap all property names and values in double quotes!

RESPONSE FORMAT:
{{
    "{key_col}": "{key_val}",
    ...
}}
"""

# --- VALIDATION ---

CHUNK_REFINEMENT_PROMPT = """
You are a Data Cleaning Assistant.
The following rows extracted from a table violate the constraint: {constraints}.

Invalid Rows:
{rows}

YOUR TASK:
1. Correct the data in these rows to satisfy the constraint IF POSSIBLE (e.g. fix year, normalize name).
2. If a row is completely irrelevant or cannot be fixed, DROP it.
3. Return ONLY the fixed rows in JSON format.

Return JSON object: {{ "fixed_rows": [...] }}
"""

ENTITY_RESOLUTION_PROMPT = """
You are an Entity Resolver.
The following rows might represent the SAME entity but have conflicting or redundant info:
{rows}

YOUR TASK:
1. Determining if they are indeed duplicates (referring to the same real-world entity).
2. If yes, MERGE them into a SINGLE canonical row. Resolve conflicts by choosing the most detailed/accurate value.
3. If they are distinct entities, keep them separate.

Return JSON object: {{ "resolved_rows": [...] }}
"""

GEN_ASSERTION_PROMPT = """
You are a QA Engineer interacting with a Python Data Validator.
Your goal is to write a set of Python `assert` statements to validate a row of data.

Context:
Schema/Description: {schema_desc}
Sample Row: {sample_row}

Instructions:
1. Write 3-5 critical assertions that must hold true for this data.
2. Focus on numerical ranges, data types, date formats, and non-empty fields.
3. Assume the variable is named `row`.
4. Do NOT use external libraries except `re` (regular expressions) or standard `datetime`.
5. **IMPORTANT**: Do NOT invent strict mathematical formulas between columns (e.g. `Total = A + B`) unless the relationship is completely obvious and universal. Real-world data often has noise. Prefer looser bounds (e.g. `Total >= A`, `Total >= B`).
6. Return ONLY the python code block.

Example:
assert row['Population'] > 0, "Population must be positive"
assert '20' in row['Date'], "Date must be 20th century or later"

Code:
"""

FIX_DATA_PROMPT = """
You are a Data Repair Agent.
We found errors in the following data rows based on python assertions.

Error Report:
{error_report}

Instructions:
1. Fix the values in the rows to satisfy the assertions. 
2. If a value is unfixable (e.g. clearly garbage), set it to None or empty string.
3. Return corrected rows as a JSON object:
   {{ "fixed_rows": [{{"Column1": "Value1", ...}}, ...] }}
"""

VALUE_VALIDATION_PROMPT = """
Task: Validate the following values extracted from a column against a specific constraint rule and general quality standards.

Constraint Rule: "{constraint}"
Column Name: "{column}"
Data Type: "{dtype}"
Semantic Type: "{semantic_type}"
Formatting: "{formatting}"

Values to Check (Unique Samples):
{values}

Validation Criteria:
1. **Constraint Check**: Does the value satisfy the rule? (e.g. "Year 2000-2010", "Name starts with A")
2. **Type/Format Check**: Is the value consistent with the expected Data Type and Formatting? (e.g. integer, YYYY-MM-DD)
3. **Semantic Check**: Is the value relevant to the Semantic Type? (e.g. "City" cannot be "123")
4. **Null Check**: Is the value a placeholder for missing data? (e.g. "N/A", "None", "null", "")

Return JSON object with a SINGLE list of invalid values:
{{
    "invalid_values": ["val1", "val2", "N/A"]
}}
If all values are valid, return "invalid_values": [].
"""

DATA_EXTRACTION_COLUMNS_SUFFIX = """
Extract the following columns: {cols}.
Return a valid JSON Object with key "items": {{"items": [...]}}.
"""

# --- COMPLETENESS & RESOLUTION ---

COMPLETENESS_CHECK_PROMPT = """
Task: Determine if the extracted data is "Complete Enough" compared to the expected estimation according to {source}.

Query: "{query}"
Expected Row Count (Estimate): {estimated_rows}
Actual Extracted Count: {actual_rows}

Context:
- The estimate might be rough (e.g. 1000 for "Pokemon", 50 for "States").
- The extraction might be partial due to pagination limits or model refusal.
- If Actual is very close to Expected (e.g. 150 vs 151), it is COMPLETE.
- If Actual is significantly lower (e.g. 10 vs 100), it is INCOMPLETE.
- If Actual > Expected, it is likely COMPLETE (possibly with duplicates).
- If Actual = 0 and Expected > 0, it is INCOMPLETE (extraction failed entirely).

Decision:
Returns JSON:
{{
    "is_complete": true/false,
    "reason": "..."
}}
"""

FORMAT_NORMALIZATION_PROMPT = """
Task: Normalize values in column "{column}" to match the target type "{dtype}" and format "{formatting}".

Values to Normalize (Unique Samples):
{values}

Instructions:
1. **Integers**: Strip non-numeric text. (e.g. "Generation 1" -> 1, "ID: 5" -> 5).
2. **Dates**: Convert to requested format (e.g. "January 1st, 2020" -> "2020-01-01").
3. **Strings**: Fix casing if specified, but usually keep as is unless it looks like a list extraction artifact.
4. **Clean**: Remove quotes if strictly numeric column.

Return JSON mapping ONLY for values that need changing:
{{
    "corrections": {{
        "Generation 1": 1,
        "ID: 5": 5
    }}
}}
"""

ENTITY_RESOLUTION_DECISION_PROMPT = """
Task: Resolve potential duplicates for Entity "{entity_name}" according to {source}.

Conflict Details:
{conflicts}

Context:
- We found multiple records for "{entity_name}" that differ in Primary Key or other attributes.
- Use the "Constraints" to determine which one is likely correct.
- If one looks like a hallucination (e.g. ID doesn't match known pattern), discard it.
- If strictly ambiguous, keep the most detailed one.

Decision:
Return JSON:
{{
    "keep_index": <int> // 0-based index from the conflict list, or the ID if easier. We used list index in code.
}}
"""

DEDUPLICATION_RESOLUTION_PROMPT = """
Task: Resolve metadata conflicts for the same Primary Key Entity according to {source}.

Entity:
{entity}

Conflicting Records:
{conflicts}

Context:
- Multiple records claim to be the same Entity (same ID/PK) but have different attributes.
- Your job is to select the BEST record to keep.
- "Best" means: Most complete, most consistent with constraints (if any), and least likely to be a hallucination.
- If all are similar, pick the most detailed one.

Decision:
Return JSON:
{{
    "keep_index": <int> // 0-based index from the Conflicting Records list to KEEP.
}}
"""
