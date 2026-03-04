# PKDB Framework: Design and Logic Flow Documentation

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Core Components](#core-components)
4. [Detailed Logic Flow](#detailed-logic-flow)
5. [Data Structures](#data-structures)
6. [Execution Modes](#execution-modes)
7. [Validation Pipeline](#validation-pipeline)
8. [Configuration](#configuration)

---

## Overview

**PKDB (Parametric Knowledge Database)** is an LLM-based framework for extracting structured tables from parametric knowledge. The framework uses a Map-Reduce architecture to handle large-scale data extraction by automatically partitioning queries into smaller, manageable sub-queries.

### Key Features

- **Automatic Schema Inference**: Infers table structure from natural language queries
- **Intelligent Partitioning**: Automatically decomposes large queries into partitions
- **Map-Reduce Execution**: Parallel processing of partitions for efficiency
- **Multi-Stage Validation**: Ensures data quality through constraint checking, deduplication, and format normalization
- **Flexible Execution Modes**: Supports both batch and row-by-row extraction strategies

---

## Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        User Query                            │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    PKDB Interface                             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  QueryAnalyzer: Analyze & Rewrite Query              │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                     │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │  SchemaGenerator: Infer Table Schema                 │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                     │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │  DataPlanBuilder: Create Execution Plan             │   │
│  │  - Partition Strategy Selection                     │   │
│  │  - Recursive Task Generation                        │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                     │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │  MapReduceEngine: Execute Plan                       │   │
│  │  - Map Phase: Parallel Partition Execution          │   │
│  │  - Reduce Phase: Aggregate Results                  │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                     │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │  GlobalValidator: Clean & Validate                  │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                     │
└─────────────────────────┼─────────────────────────────────────┘
                          │
                          ▼
                   DataFrame Result
```

### Component Interaction Flow

```
User Query
    │
    ├─→ QueryAnalyzer.analyze()
    │   ├─→ Extract constraints & collection strategy
    │   ├─→ Rewrite query for clarity
    │   └─→ Estimate row count
    │
    ├─→ SchemaGenerator.infer_schema()
    │   ├─→ Generate column pool (15+ attributes)
    │   ├─→ Generate schema candidates (k=3)
    │   ├─→ Rank candidates (LLM-as-Judge)
    │   └─→ Enrich with types & descriptions
    │
    ├─→ DataPlanBuilder.create_plan()
    │   ├─→ Decide: Flat vs Partitioned
    │   ├─→ If Partitioned:
    │   │   ├─→ Generate Partition Key Candidates:
    │   │   │   ├─→ If schema provided: All columns are candidates
    │   │   │   └─→ If no schema: Joint Optimization
    │   │   │       ├─→ Generate schema candidates
    │   │   │       └─→ Extract partition keys from each schema
    │   │   ├─→ Evaluate Candidates (if evaluated mode):
    │   │   │   ├─→ Estimate distribution for each candidate
    │   │   │   └─→ Score candidates
    │   │   ├─→ Select best partition key (LLM or scoring)
    │   │   └─→ Recursively generate map tasks
    │   └─→ Return DataPlanRequest
    │
    ├─→ MapReduceEngine.execute()
    │   ├─→ Map Phase (Parallel):
    │   │   ├─→ Execute each DataTask
    │   │   ├─→ Batch Mode: Single LLM call per partition
    │   │   ├─→ Row-by-Row Mode: Keys → Rows (2-step)
    │   │   ├─→ Pagination & Completeness Check
    │   │   └─→ Map-side Validation
    │   ├─→ Reduce Phase:
    │   │   ├─→ Aggregate all partition results
    │   │   └─→ Schema alignment
    │   └─→ Global Validation
    │
    └─→ Return DataFrame + Metrics
```

---

## Core Components

### 1. PKDB (Interface)

**Location**: `src/interface.py`

**Purpose**: Main entry point for the framework. Orchestrates all components.

**Key Methods**:

- `extract_table(query, schema, execution_mode)`: Main API method

**Initialization**:

```python
PKDB(
    model="gpt-4o",
    cache_dir=".cache_pkdb",
    max_concurrent=5,
    row_threshold=120,
    decomposition_strategy="rule_based",
    partitioning_method="evaluated"
)
```

**Component Initialization**:

- `LLMClient`: Handles all LLM API calls with caching
- `SchemaGenerator`: Infers table schemas
- `DataPlanBuilder`: Creates execution plans
- `MapReduceEngine`: Executes plans
- `GlobalValidator`: Validates and cleans data

---

### 2. QueryAnalyzer

**Location**: `src/analysis.py`

**Purpose**: Analyzes user queries to extract structured information and estimate complexity.

**Key Methods**:

- `analyze(query)`: Full analysis pipeline
- `analyze_to_task(query)`: Converts query to DataTask
- `_estimate_row_count(query)`: Estimates result size
- `should_decompose(query, estimated_rows)`: Decides if partitioning needed

**Process**:

1. **Query Analysis**: Extracts constraints, collection strategy, and refined query
2. **Query Rewriting**: Clarifies ambiguities and makes query more precise
3. **Row Estimation**: Estimates total number of rows expected
4. **Decomposition Decision**: Determines if Map-Reduce is needed

**Output**: `QueryAnalysisResult` containing:

- `rewritten_query`: Clarified query
- `estimated_rows`: Expected row count
- `task`: Structured `DataTask` object

---

### 3. SchemaGenerator

**Location**: `src/schema.py`

**Purpose**: Infers table schema (columns, types, primary keys) from queries.

**Key Methods**:

- `infer_schema(query, strategy)`: Main entry point
- `generate_pool(query)`: Creates attribute pool (~15 attributes)
- `generate_candidates(query, k)`: Generates k schema candidates
- `_rank_candidates(query, candidates)`: Ranks candidates using LLM
- `infer_enrichment(query, schema_dict)`: Enriches schema with types

**Process**:

1. **Pool Generation**: Brainstorms 15+ potential attributes (visible + hidden)
2. **Candidate Generation**: Creates k=3 different schema proposals
3. **Ranking**: Uses LLM-as-Judge to select best schema
4. **Enrichment**: Infers semantic types, data types, formatting, and primary keys

**Schema Structure**:

```python
SchemaDefinition(
    table_name: str,
    columns: List[Column],
    primary_keys: List[str]
)

Column(
    name: str,
    description: str,
    semantic_type: str,  # text, categorical, numerical, time, entity
    data_type: str,      # int, float, date, str, bool
    formatting: str      # "YYYY-MM-DD", "$", etc.
)
```

**Strategies**:

- `"first"`: Use first candidate
- `"llm_rank"`: LLM-based ranking (default)

---

### 4. DataPlanBuilder

**Location**: `src/dataplan.py`

**Purpose**: Creates execution plans by determining partitioning strategy and generating tasks.

**Key Methods**:

- `create_plan(query, schema, ...)`: Main planning method
- `_create_plan_recursive(task, schema, depth)`: Recursive task generation
- `_fetch_partition_values(query, key)`: Gets partition values
- `_construct_natural_language_subquery(...)`: Rewrites sub-queries

**Partition Candidate Generation**:

The framework generates partition key candidates before evaluation:

1. **When Schema is Provided**:
   - All columns in the schema become candidates
   - Example: Schema with [Name, State, Population] → 3 candidates

2. **When No Schema (Joint Optimization)**:
   - Generates k=3 schema candidates
   - For each schema candidate:
     - Tries to extract `partition_strategy` field (if schema generator provided it)
     - **Fallback**: If `partition_strategy` is empty, uses **all columns** in that schema
   - Creates (Schema, PartitionKey) pairs → StrategyCandidates
   - Example: 3 schemas × 2-3 keys each → 6-9 candidates

**Important**: In "evaluated" mode, the framework evaluates **candidates**, not necessarily all columns. However, if the schema generator doesn't provide a `partition_strategy` field, it falls back to evaluating all columns.

**Planning Process**:

#### Step 1: Decomposition Decision

- **Rule-based**: `estimated_rows > row_threshold` → partition
- **LLM-based**: Ask LLM if Map-Reduce needed

#### Step 2: Schema Selection

- If schema provided: Use it directly
- If not: Run Joint Optimization (Schema + Partition Key)

#### Step 3: Partition Key Candidate Generation (if decomposition needed)

**When Schema is Provided:**

- All columns in the schema become partition key candidates
- Example: Schema has [Name, State, Population] → 3 candidates

**When No Schema (Joint Optimization):**

1. Generate column pool (~15 attributes)
2. Generate k=3 schema candidates
3. For each schema candidate:
   - Extract `partition_strategy` field (if schema generator provided it)
   - If empty, use **all columns** in that schema as fallback
4. Create StrategyCandidates: (Schema, PartitionKey) pairs
   - Example: 3 schemas × 2-3 keys each → 6-9 candidates

#### Step 4: Partition Key Evaluation & Selection

**Evaluated Mode**:

1. **Evaluate all candidates** concurrently:
   - For each candidate, call `DistributionEstimator.estimate()`
   - Estimates: number of partitions, partition sizes, ambiguity score
2. **Score each candidate**:
   - Base score: 100.0
   - Penalties: Ambiguity (-50), too many partitions (-30), too few (-50)
   - Bonuses: Optimal partition count (5-20) (+10), good semantic types (+20)
   - Penalties: Zip codes (-80), generic IDs (-60), numerical measures (-50)
3. **Select best**:
   - **LLM selection** (`partition_selection_mode="llm"`): LLM-as-Judge picks best
   - **Scoring selection** (default): Highest score wins

**First Mode**:

- Skip candidate generation and evaluation
- Immediately use first available column as partition key

**Note**: In recursive calls (depth > 0), evaluated mode evaluates all available columns (excluding already-used partition keys).

#### Step 5: Recursive Task Generation

For each partition value:

1. Check if partition is "fat" (estimated_rows > threshold)
2. If fat and depth < max_depth:
   - Recurse with sub-partition
   - At deeper levels, partition key candidates are all unused columns
   - Evaluated mode: Evaluates all unused columns, selects best
   - First mode: Picks first unused column
3. Otherwise: Create leaf task

**Task Structure**:

```python
DataTask(
    query: str,
    constraints: dict,
    collection_strategy: str,
    schema: SchemaDefinition,
    partition_key: str,
    partition_value: str,
    estimated_rows: int,
    execution_mode: str  # "batch" or "row_by_row"
)
```

**Output**: `DataPlanRequest` containing:

- `task`: Root task
- `schema`: Final schema
- `map_tasks`: List of leaf tasks to execute

---

### 5. MapReduceEngine

**Location**: `src/engine.py`

**Purpose**: Executes the data plan using Map-Reduce pattern.

**Key Methods**:

- `execute(plan, context)`: Main execution method
- `_execute_map_task(task, context)`: Executes single partition
- `_execute_row_by_row(task, context)`: Row-by-row execution
- `_reduce(results, columns)`: Aggregates results
- `_check_completeness(task, count, prompt)`: Checks if extraction complete

**Execution Flow**:

#### Map Phase (Parallel)

For each `DataTask` in `plan.map_tasks`:

**Batch Mode** (default):

1. Generate prompt from task
2. Call LLM with extraction prompt
3. Parse JSON response
4. Handle truncation/repair if needed
5. **Completeness Check**:
   - If `actual_rows < 0.5 * estimated_rows`: Reprompt with continuation
   - Max 3 attempts
6. Enrich with partition info if missing
7. **Map-side Validation**: Run `map_validator` (ChunkConstraints)
8. Return rows + metrics

**Row-by-Row Mode**:

1. **Step 1**: Generate list of primary key values
   - Prompt: `ROW_BY_ROW_KEYS_PROMPT`
   - Extract keys from response
2. **Step 2**: For each key (parallel):
   - Prompt: `ROW_BY_ROW_FETCH_PROMPT`
   - Extract full row
3. Aggregate all rows
4. Return rows + metrics

#### Reduce Phase

1. Flatten all partition results into single list
2. Convert to DataFrame
3. Ensure schema structure (add missing columns)
4. Select columns in schema order
5. Simple deduplication (strict duplicates)
6. **Global Validation**: Run `global_validator` (Deduplication, Code, etc.)

**Completeness Strategies**:

- `"heuristic"`: `current_count >= 0.5 * estimated_rows`
- `"llm"`: Ask LLM if extraction is complete

---

### 6. GlobalValidator

**Location**: `src/validator.py`

**Purpose**: Multi-stage validation pipeline for data quality assurance.

**Architecture**: Strategy pattern with pluggable validators.

**Available Validators**:

#### ChunkValidator

- **Purpose**: Enforces partition constraints and type checking
- **Detect**: Uses LLM to validate values against constraints, types, formatting
- **Fix**: Attempts to refine violating rows via LLM, drops if unfixable

#### DeduplicationValidator

- **Purpose**: Removes duplicate rows
- **Strategies**:
  - `"first"`: Keep first, drop rest (fast)
  - `"llm-based"`: LLM resolves conflicts when duplicates differ

#### CodeValidator

- **Purpose**: Generates Python assertions, executes them, fixes violations
- **Process**:
  1. Generate assertions from schema
  2. Execute assertions on each row
  3. Fix violations via LLM

#### FormatNormalizationValidator

- **Purpose**: Normalizes column formats (e.g., "Generation 1" → 1)
- **Process**: LLM-based format correction per column

#### EntityResolutionValidator

- **Purpose**: Resolves entities with same name but different IDs
- **Process**: LLM decides which record to keep

#### EmbeddingValidator

- **Purpose**: Outlier detection using embeddings
- **Process**: Isolation Forest on row embeddings, LLM verification

**Execution Order** (configurable):

1. ChunkConstraints (map-side)
2. Deduplication (reduce-side)
3. Code (reduce-side)
4. FormatNormalization (reduce-side)
5. EntityResolution (reduce-side)

---

## Detailed Logic Flow

### Complete Extraction Flow

```
┌─────────────────────────────────────────────────────────────┐
│ 1. USER QUERY: "List all US cities"                          │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. QUERY ANALYSIS                                            │
│    ├─ Extract: constraints={"Country": "USA"}               │
│    ├─ Rewrite: "List all cities in the United States"        │
│    └─ Estimate: ~19,000 rows                               │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. SCHEMA INFERENCE                                          │
│    ├─ Pool: [Name, State, Population, Area, ...]           │
│    ├─ Candidates: 3 schemas                                 │
│    ├─ Rank: Select best                                     │
│    └─ Enrich: Types, formatting, primary keys              │
│                                                              │
│    Result: SchemaDefinition(                                │
│      table_name="cities",                                   │
│      columns=[                                              │
│        Column("Name", semantic_type="entity", ...),         │
│        Column("State", semantic_type="categorical", ...),   │
│        Column("Population", data_type="int", ...)           │
│      ],                                                     │
│      primary_keys=["Name"]                                  │
│    )                                                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. PLANNING                                                  │
│    ├─ Decision: estimated_rows (19000) > threshold (120)     │
│    │  → Requires decomposition                              │
│    ├─ Generate Partition Key Candidates:                      │
│    │  ├─ Schema candidates: 3 schemas                        │
│    │  ├─ Extract partition keys from each schema:           │
│    │  │  Schema1: ["State", "Region"]                       │
│    │  │  Schema2: ["State", "Population"]                   │
│    │  │  Schema3: ["Name", "State"]                         │
│    │  └─ StrategyCandidates: 6 (Schema, Key) pairs           │
│    ├─ Evaluate Candidates (Evaluated Mode):                  │
│    │  ├─ Evaluate: (Schema1, "State") → Score: 85           │
│    │  ├─ Evaluate: (Schema1, "Region") → Score: 60          │
│    │  ├─ Evaluate: (Schema2, "State") → Score: 82           │
│    │  ├─ Evaluate: (Schema2, "Population") → Score: 40      │
│    │  └─ ... (all 6 candidates)                             │
│    ├─ Select Best: (Schema1, "State") with score 85          │
│    ├─ Fetch Partition Values:                               │
│    │  ["Alabama", "Alaska", ..., "Wyoming"] (50 states)    │
│    └─ Generate Tasks:                                        │
│       ├─ Task1: query="List US cities in Alabama", ...      │
│       ├─ Task2: query="List US cities in Alaska", ...       │
│       └─ ... (50 tasks)                                     │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. MAP PHASE (Parallel Execution)                           │
│                                                              │
│    Task1 (Alabama):                                         │
│    ├─ Prompt: "List all US cities in Alabama..."           │
│    ├─ LLM Response: {"items": [                            │
│    │    {"Name": "Birmingham", "State": "Alabama", ...},   │
│    │    {"Name": "Montgomery", "State": "Alabama", ...},    │
│    │    ...                                                 │
│    │  ]}                                                    │
│    ├─ Parse: 200 rows extracted                            │
│    ├─ Completeness: 200 >= 0.5 * 380 → Complete           │
│    └─ Map Validation: Check constraints, types              │
│                                                              │
│    Task2 (Alaska): ... (parallel)                          │
│    ...                                                      │
│    Task50 (Wyoming): ... (parallel)                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. REDUCE PHASE                                              │
│    ├─ Aggregate: Combine all 50 partition results          │
│    ├─ Schema Alignment: Ensure all columns present          │
│    └─ Simple Dedup: Remove exact duplicates                 │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. GLOBAL VALIDATION                                         │
│    ├─ DeduplicationValidator:                               │
│    │  └─ Remove duplicates on primary key "Name"            │
│    ├─ CodeValidator:                                        │
│    │  ├─ Generate: assert row['Population'] > 0            │
│    │  ├─ Execute: Find 5 violations                       │
│    │  └─ Fix: LLM corrects values                          │
│    └─ FormatNormalizationValidator:                        │
│       └─ Normalize "Population" to integers                  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ 8. RESULT: DataFrame with ~19,000 rows                      │
│    Columns: Name, State, Population, ...                    │
└─────────────────────────────────────────────────────────────┘
```

### Recursive Partitioning Example

For a very large partition (e.g., "California" with 500 cities):

```
Root Task: "List all US cities"
    │
    ├─ Partition by State → 50 tasks
    │
    └─ Task: "List US cities in California" (estimated: 500 rows)
           │
           ├─ Check: 500 > threshold (120) → Fat partition
           ├─ Recurse with depth=1
           │
           └─ Sub-partition by Region:
              ├─ Task: "List US cities in California, Northern"
              ├─ Task: "List US cities in California, Central"
              └─ Task: "List US cities in California, Southern"
```

---

## Data Structures

### DataTask

```python
@dataclass
class DataTask:
    # Goal Definition
    query: str
    constraints: dict
    collection_strategy: str  # "ALL", "LIMIT N", "MOST_COMMON"
    
    # Context
    schema: Optional[SchemaDefinition]
    parent: Optional[DataTask]
    trace_id: str
    
    # Partitioning
    partition_key: Optional[str]
    partition_value: Optional[str]
    estimated_rows: Optional[int]
    
    # Execution
    execution_mode: str  # "batch" or "row_by_row"
    source: str
```

### SchemaDefinition

```python
@dataclass
class SchemaDefinition:
    table_name: str
    columns: List[Column]
    primary_keys: List[str]
```

### Column

```python
@dataclass
class Column:
    name: str
    description: str
    semantic_type: str  # text, categorical, numerical, time, entity
    data_type: str      # int, float, date, str, bool
    formatting: str     # "YYYY-MM-DD", "$", etc.
```

### DataPlanRequest

```python
@dataclass
class DataPlanRequest:
    task: DataTask  # Root task
    schema: SchemaDefinition
    map_tasks: List[DataTask]  # Leaf tasks to execute
```

### DistributionStats

```python
@dataclass
class DistributionStats:
    key_name: str
    num_partitions: int
    max_partition_size: int
    ambiguity_score: float  # 0.0 (clear) to 1.0 (ambiguous)
    reasoning: str
    partition_counts: Dict[str, int]  # Value → estimated count
```

---

## Execution Modes

### Batch Mode (Default)

- **Use Case**: Most queries, especially when schema is well-defined
- **Process**: Single LLM call per partition
- **Prompt**: Comprehensive extraction prompt with all columns
- **Advantages**: Faster, fewer API calls
- **Limitations**: May truncate for very large partitions

### Row-by-Row Mode

- **Use Case**: When primary keys are known, need detailed per-row extraction
- **Process**:
  1. Generate list of keys (one LLM call)
  2. Fetch full row for each key (parallel LLM calls)
- **Advantages**: More reliable for large datasets, no truncation
- **Limitations**: More API calls, slower for small datasets

**Selection**: Can be specified per task or globally via `execution_mode` parameter.

---

## Validation Pipeline

### Map-Side Validation

Runs on each partition result before aggregation:

- **ChunkValidator**: Enforces partition constraints (e.g., all rows must have State="Alabama")

### Reduce-Side Validation

Runs on final aggregated result:

1. **DeduplicationValidator**: Removes duplicates on primary key
2. **CodeValidator**: Executes generated assertions
3. **FormatNormalizationValidator**: Normalizes column formats
4. **EntityResolutionValidator**: Resolves entity conflicts
5. **EmbeddingValidator**: Detects outliers (optional)

**Configuration**:

```python
# Map-side validators
map_validators = ["ChunkConstraints"]

# Reduce-side validators
global_validators = ["Deduplication", "Code", "FormatNormalization"]
```

---

## Configuration

### PKDBConfig

```python
@dataclass
class PKDBConfig:
    model: str = "gpt-4o"
    cache_dir: str = ".cache_pkdb"
    max_concurrent: int = 5
    use_cache: bool = True
    max_partitions: int = 200
    num_schema_candidates: int = 3
    schema_temperature: float = 0.7
    extraction_temperature: float = 0.0
    decomposition_strategy: str = "rule_based"  # "rule_based" or "llm_based"
    row_threshold: int = 120
    max_depth: int = 2
    partitioning_method: str = "evaluated"  # "evaluated" (evaluates candidates) or "first" (picks first column)
    max_total_requests: int = 10000
    default_execution_mode: str = "batch"
```

### Key Parameters

- **row_threshold**: Threshold for triggering decomposition (default: 120)
- **max_partitions**: Maximum number of partitions to create (default: 200)
- **max_depth**: Maximum recursion depth for partitioning (default: 2)
- **partitioning_method**:
  - `"evaluated"`: Joint optimization of schema + partition key (evaluates all candidates)
  - `"first"`: Use first available partition key (no evaluation, faster)
- **decomposition_strategy**:
  - `"rule_based"`: Use row count threshold
  - `"llm_based"`: Ask LLM if decomposition needed

---

## Error Handling & Robustness

### JSON Parsing

- Handles truncation by repairing incomplete JSON
- Falls back to partial extraction if repair fails

### Completeness Checking

- Compares actual vs estimated rows
- Reprompts if extraction incomplete (max 3 attempts)
- Uses heuristic (50% rule) or LLM-based check

### Validation Failures

- Validators log errors but don't crash pipeline
- Invalid rows are dropped or fixed via LLM
- Falls back to dropping if fix fails

### Partition Failures

- Individual partition failures don't stop execution
- Failed partitions return empty results
- Final result aggregates successful partitions

---

## Performance Optimizations

1. **Concurrent Execution**: All partitions execute in parallel
2. **Caching**: LLM responses cached to avoid redundant calls
3. **Evaluated Partitioning**: Avoids "dust" (too many small partitions) and "fat" (too few large partitions)
4. **Batch Validation**: Validators process data in batches when possible
5. **Early Termination**: Completeness checks prevent unnecessary reprompts

---

## Artifact Persistence

The framework saves execution artifacts when `output_dir` is provided in context:

- `schema.json`: Schema inference process and final schema
- `plan.json`: Execution plan with all tasks
- `tasks.json`: All map tasks
- `tasks_io.jsonl`: All LLM prompts and responses
- `validation_log.jsonl`: Validation events and decisions

---

## Summary

PKDB is a sophisticated framework that:

1. **Analyzes** queries to extract structure and estimate complexity
2. **Infers** table schemas using multi-step LLM reasoning
3. **Plans** execution by intelligently partitioning large queries
4. **Executes** partitions in parallel using Map-Reduce pattern
5. **Validates** data through multiple stages to ensure quality
6. **Optimizes** for cost and accuracy through evaluated partitioning and caching

The framework handles queries ranging from simple lists (e.g., "7 continents") to complex large-scale extractions (e.g., "all US cities") by automatically adapting its strategy based on query complexity.
