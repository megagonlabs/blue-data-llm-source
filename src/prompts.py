# Centralized Prompts for PKDB Framework

# --- QUERY ANALYSIS ---

QUERY_ANALYSIS_PROMPT = """
User Query: "{query}"

Analyze this query and extract:
1. **Refined Query**: A clear, standalone natural language task description.
2. **Constraints**: A JSON dictionary of hard filters (e.g. {{"Year": "2023", "Country": "USA"}}). Use empty {{}} if none.
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

Guidelines:
- Clarify ambiguous terms.
- Specify that where the query want a "complete list" / "all items" or just some (like 100).
- Mention including key attributes if implied.

Output Requirements: 
- Return ONLY the rewritten query text.
- NO quotes, NO explanation, NO JSON.

Input Query: "{query}"
"""

ROW_ESTIMATION_PROMPT = """
Query: "{query}"

Estimate the total number of rows (items) that would be in the FULL result table for this query.
Think about the comprehensive nature of the request.
- If it asks for "US States", that's naturally ~50.
- If it asks for "Pokemon", that's ~1000.
- If it asks for "Movies", "Cities", "People", or "Events" over a long time range, the number could be LARGE (100+ or 1000+).
- Do NOT underestimate. It is safer to overestimate for robustness.

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
4. Partition Strategy: A list of columns to use for query decomposition, ordered by hierarchy (e.g. ["Year", "Month"]).

Guidelines:
1. **Entity Centric**: The schema MUST include the main Entity Name (e.g. "City", "Pokemon Name").
2. **Decomposition Ready**: Choose attributes that make good Partition Keys (e.g. "Region", "Type", "Year").
3. **Scope Aware**: Respect constraints (e.g. if "US Cities", "Country" is a static attribute/constraint, maybe less useful for partitioning).
4. **Complete**: Ensure all requested data can be stored.

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
            "primary_keys": ["Attribute1"],
            "partition_strategy": ["Attribute1", "Attribute2"]  # Architecture V2: List distinct keys in order of hierarchy
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
- the partitions under this schema are independent and can be processed in parallel
- the partitions under this schema are roughly equal in size
- Atomicity (columns are not composite)
- Partition Strategy Viability: Does the proposed partition strategy (if any) make sense for the query?
- Orthogonality (no redundant columns)
- Coverage (captures all necessary attributes in the query)

BAD Schemas:
- Only Numeric stats (no Names)
- Only IDs and Foreign Keys
- Missing crucial grouping columns

Return ONLY the index (0-based) of the best schema.
"""

SCHEMA_TYPE_INFERENCE_PROMPT = """
Query: "{query}"
Table: {table_name}
Columns: {columns_str}

For each column, determine:
1. Semantic Type (categorical, numerical, time, entity, text)
2. Is it a good Partition Key? 
   - Good keys divide data into chunks (e.g. State, Year, Genre, Generation, Phase).
   - BAD keys: Unique IDs (e.g. 1, 2, 3...), High cardinality names (City Name), Booleans (too few).
   
Return JSON:
{{
    "columns": {{
        "ColumnName": {{"type": "...", "is_partition": true/false}},
        ...
    }}
}}
"""

# --- ESTIMATION & STRATEGY ---

DISTRIBUTION_ESTIMATION_PROMPT = """
Your Task: Analyze the data distribution for the partition key "{key_name}" given the query "{query}".

Guidelines:
1. **Estimate Rows**: List the likely partitions and estimate the number of rows (items) in each.
2. **Assess Ambiguity**: Is this key clear and mutually exclusive? (0.0=Clear, 1.0=Ambiguous).
3. **Common Sense Scope**: 
   - Pay attention to the query scope and constrains. If the query implies a specific scope or constrains (e.g. "US Cities"), corresponding partition keys like "Country" could be limited (e.g. only "USA").
   - Do NOT hallucinate values outside the query scope.

Return JSON Code:
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
"""

PARTITION_VALUES_PROMPT = """
Reference Query: "{query}"
Target Attribute: "{key_name}" ({key_description})

Task: List ALL possible values for "{key_name}" that are STRICTLY relevant to the query scope.

CRITICAL RULES:
1. **Scope Enforcement**: If the query limits the scope, only list values within that scope.
2. **Completeness**: If the scope is broad, list ALL possible values according to common sense and query context. If there are too many (>100), list the most common or most relevant 100 values.
3. **Relevance**: Only list values that actually contain data for this query.

Format: Return ONLY a simple list of strings, one per line. No numbering, no bullets.
"""

SUBQUERY_REWRITE_PROMPT = """
Base Query: "{base_query}"
Filter: {key} = {value}

Rewrite the query to naturally include the filter.
Examples:
- Query: "List all Pokemon", Filter: Type = Fire -> "List all Fire-type Pokemon"
- Query: "List US Cities", Filter: State = CA -> "List all US Cities in California"
- Query: "List Nobel Winners", Filter: Field = Physics -> "List all Nobel Prize winners in Physics"

Return ONLY the rewritten query text.
"""

# --- EXECUTION & EXTRACTION ---

# Note: Task prompt is constructed dynamically in DataTask.to_prompt() due to high variability
# But we can extract the template parts if needed. For now keeping dynamic.

DATA_EXTRACTION_PROMPT = """
User Request: "{query}"
Target Scope:
{constraint_str}
{estimate_str}

**CRITICAL INSTRUCTION**: Extract ALL data matching the request.
You must be as comprehensive as possible. If the table usually has many rows, return MANY rows.

**OUTPUT FORMAT**:
Return ONLY the raw JSON list (or CSV) containing the data. 
DO NOT include any text like "Here is the table" or markdown code blocks.
Just the raw data.
"""

DATA_EXTRACTION_PARTITION_CONTEXT = """
CONTEXT:
This is a PARTITIONED sub-task.
You are focusing ONLY on the subset defined by:
**{partition_key} = {partition_value}**

Ensure all returned rows adhere to this partition filter.
"""

ROW_BY_ROW_KEYS_PROMPT = """
    You are a retriever of facts.
    We want to create a table with the detailed information about {query}.
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
    We want to create a table with the detailed information about {query}.
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
1. Correct the data in these rows to satisfy the constraint IF POSSIBlE (e.g. fix year, normalize name).
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
3. Return corrected rows as a JSON list.
"""

DATA_EXTRACTION_COLUMNS_SUFFIX = """
Extract the following columns: {cols}.
Return as JSON object with key "items": {{"items": [...]}}.
"""
