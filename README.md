# PKDB: Parametric Knowledge Database Agent

## Overview

PKDB is an **Agentic Data Extraction Framework** that transforms high-level natural language queries into structured datasets (DataFrames) by leveraging LLM Parametric Knowledge. Unlike traditional RAG (Retrieval Augmented Generation), PKDB extracts information directly from the model's internal knowledge base, optimizing for high-recall, structured output using techniques like Map-Reduce decomposition and Chain-of-Thought (CoT) schema generation.

**Key Features:**
*   **Auto-Partitioning:** Automatically decomposes broad queries (e.g., "List all US Cities") into parallelizable sub-tasks using **LLM-based decomposition**.
*   **Dynamic Schema Generation:** Infers optimal table structures (Columns, Types, Primary Keys) on the fly.
*   **Joint Optimization:** Planner simultaneously evaluates Schema and Partitioning strategies to minimize fan-out and maximize relevance.
*   **Robust Validation:** Includes a built-in `DataValidator` pipeline with strategies for **Entity Resolution**, **Deduplication**, and **Code-Based Verification**.
*   **Smart Pagination:** Runtime engine uses probability estimates to detect truncation and autonomously fetch remaining pages.
*   **Agentic Interface:** Designed to run autonomously or as a tool for other agents.

## Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/megagon-internal/blue-llmsource.git
    cd blue-llmsource
    ```

2.  Create and activate a virtual environment (recommended):
    ```bash
    conda create -n pkdb python=3.10
    conda activate pkdb
    ```

3.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

4.  Set up your OpenAI API Key:
    ```bash
    export OPENAI_API_KEY="sk-..."
    ```

## Usage

### Basic Example

```python
import asyncio
from src.interface import PKDB, PKDBConfig

async def main():
    # Initialize the client (Defaults to GPT-4o)
    pkdb = PKDB()

    # Define your query
    query = "List all Pokémon from Generation 1 to Generation 3."

    # Execute extraction
    df, metrics, plan_info = await pkdb.extract_table(query)

    print(f"Extracted {len(df)} rows.")
    print(df.head())
    print(f"Cost: ${metrics.cost:.4f}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Advanced Configuration

You can customize the execution behavior using `PKDBConfig`.

```python
config = PKDBConfig(
    model="gpt-4o",
    max_partitions=200,           # Max partitions per split
    row_threshold=120,            # Row estimate threshold to trigger decomposition
    decomposition_strategy="rule_based",  # 'rule_based' or 'llm_based'
    partitioning_method="llm_based",      # 'llm_based', 'first', or 'semantic'
    partition_selection_mode="llm",       # 'llm' or 'scoring'
    default_execution_mode="batch",       # 'batch' or 'row_by_row'
    dedup_strategy="llm_based",           # 'first' or 'llm_based'
    source="Wikipedia",                   # Configurable data source label
    max_concurrent=15,                     # Async concurrency limit
    max_depth=2                           # Max recursive decomposition depth
)

pkdb = PKDB(config)
```

### Running Benchmarks

The `experiments/` directory contains scripts to reproduce research benchmarks (WikiTabGen, etc.).

```bash
# Run the Pokemon debug script
python experiments/run_pokemon.py

# Run a mini benchmark
python experiments/run_mini_dataset.py
```

**Available Arguments:**

| Argument | Default | Description |
| :--- | :--- | :--- |
| `--model` | `gpt-4o` | The LLM capability model to use. |
| `--limit` | `3` | Max number of tables to evaluate (use `0` for all). |
| `--table` | `None` | Run a specific table by name (overrides limit). |
| `--decomposition_strategy` | `rule_based` | Strategy for query decomposition: `rule_based` or `llm_based`. |
| `--row_threshold` | `120` | Threshold of estimated rows to trigger verification/partitioning. |
| `--execution_mode` | `batch` | Execution mode: `batch` (parallel) or `row_by_row` (sequential). |
| `--partition_selection_mode` | `llm` | Method for selecting partition keys: `scoring` or `llm`. |
| `--plan_only` | `False` | Run only the planning phase (skip data fetching). |
| `--use_cache` | `False` | Enable caching for LLM responses. |
| `--resume_dir` | `None` | Directory to resume a previous interrupted run from. |


## Architecture

*   **`src/interface.py`**: Main entry point (`PKDB` class). Handles configuration and component wiring.
*   **`src/analysis.py`**: `QueryAnalyzer` for intent understanding and initial row estimation.
*   **`src/schema.py`**: `SchemaGenerator` for inferring optimal table structures (Columns, Types, Primary Keys).
*   **`src/dataplan.py`**: `DataPlanBuilder` (Planner). Implements **Joint Optimization** to simultaneously evaluate Schema and Partitioning strategies. Recursively decomposes queries into a task graph.
*   **`src/engine.py`**: `MapReduceEngine`. Manages async execution of the task graph, handling concurrency, rate limits, and **Smart Pagination**.
*   **`src/validator.py`**: `DataValidator`. Orchestrates a pipeline of cleaning strategies:
    *   **`ChunkValidator`**: Verifies partition integrity (e.g., Year=2020).
    *   **`FormatNormalizationValidator`**: Standardizes values (e.g., "Generation 1" -> 1).
    *   **`EntityResolutionValidator`**: Deduplicates entities based on content similarity and constraints.
    *   **`CodeValidator`**: Generates and executes Python assertions for data constraints.
