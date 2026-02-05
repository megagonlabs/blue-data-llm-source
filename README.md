# PKDB: Parametric Knowledge Database Agent

## Overview

PKDB is an **Agentic Data Extraction Framework** that transforms high-level natural language queries into structured datasets (DataFrames) by leveraging LLM Parametric Knowledge. Unlike traditional RAG (Retrieval Augmented Generation), PKDB extracts information directly from the model's internal knowledge base, optimizing for high-recall, structured output using techniques like Map-Reduce decomposition and Chain-of-Thought (CoT) schema generation.

**Key Features:**
*   **Auto-Partitioning:** Automatically decomposes broad queries (e.g., "List all US Cities") into parallelizable sub-tasks (by Region, State, etc.) to overcome LLM context limits.
*   **Dynamic Schema Generation:** Infers the optimal table structure (Columns, Types, Primary Keys) on the fly, or adapts to a user-provided schema.
*   **Interactive Planning:** Uses a multi-step planning process to estimate result size (`DistributionEstimator`) and select the best execution strategy.
*   **Robust Validation:** Includes a built-in validation pipeline (`GlobalValidator`) for deduplication, consistency checking, and code-based verification.
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
    print(f"Cost: ${metrics.total_cost:.4f}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Advanced Configuration

You can customize the execution behavior using `PKDBConfig`.

```python
config = PKDBConfig(
    model="gpt-4o",
    max_partitions=50,       # Max number of parallel tasks
    row_threshold=120,       # Threshold to trigger decomposition
    decomposition_strategy="llm_based", # 'llm_based' or 'rule_based'
    partitioning_method="smart" # 'smart' (Joint Optimization) or 'greedy'
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

## Architecture

The system is composed of several modular components:

*   **`src/interface.py`**: Main entry point (`PKDB` class).
*   **`src/analysis.py`**: `QueryAnalyzer` for intent understanding and row estimation.
*   **`src/schema.py`**: `SchemaGenerator` for inferring table structures.
*   **`src/dataplan.py`**: `DataPlanBuilder` for recursive task decomposition.
*   **`src/engine.py`**: `MapReduceEngine` for async execution management.
*   **`src/validator.py`**: `GlobalValidator` for data cleaning and verification.

For a detailed deep dive into the system design, please refer to [system_design_detailed.md](system_design_detailed.md).
