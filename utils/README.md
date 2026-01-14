# LLM Client Utilities

A comprehensive, research-grade LLM client with async/sync support, persistent caching, metrics tracking, batch processing, and robust error handling. Designed for research workflows with support for multiple providers (OpenAI, Anthropic, Fireworks, Ollama, etc.).

## Installation

**Required dependencies:**

```bash
pip install litellm tenacity diskcache
```

**Optional dependency (for Jupyter notebooks or active event loops):**

```bash
pip install nest_asyncio
```

Note: `nest_asyncio` is only needed if you want to use sync methods (`generate()`, `generate_batch()`, etc.) in environments with an active event loop, such as Jupyter notebooks. For async methods or regular Python scripts, it's not required.

## Quick Start

### Basic Usage

```python
from utils.llm_utils import LLMClient

# Initialize client
llm = LLMClient(model="gpt-4o-mini")

# Sync call
result = llm.generate("What is Python?")
print(result.content)
print(f"Cost: ${result.metrics.cost:.6f}")
```

### Async Usage

```python
import asyncio
from utils.llm_utils import LLMClient

async def main():
    llm = LLMClient(model="gpt-4o-mini", max_concurrent=32)
    result = await llm.agenerate("What is Python?")
    print(result.content)

asyncio.run(main())
```

## Features

### 1. Async/Sync Support

**Async (recommended for batches):**

```python
llm = LLMClient(model="gpt-4o-mini")
result = await llm.agenerate("Explain quantum computing")
```

**Sync (for simple scripts):**

```python
llm = LLMClient(model="gpt-4o-mini")
result = llm.generate("Explain quantum computing")
```

### 2. Batch Processing

**Async batch:**

```python
prompts = ["What is AI?", "What is ML?", "What is NLP?"]
results = await llm.agenerate_batch(prompts, show_progress=True)
```

**Sync batch:**

```python
results = llm.generate_batch(prompts)
```

### 3. Caching

Responses are automatically cached to reduce costs:

```python
# First call - hits API
result1 = await llm.agenerate("What is Python?")
print(f"Cached: {result1.cached}")  # False

# Second call - uses cache
result2 = await llm.agenerate("What is Python?")
print(f"Cached: {result2.cached}")  # True
```

**Cache refresh (for voting/self-consistency):**

```python
# Force fresh response even if cached
result = await llm.agenerate("What is Python?", cache_refresh=True)
```

### 4. Voting/Self-Consistency

Generate multiple diverse responses for voting. By default, caching is disabled to ensure diversity:

```python
samples = await llm.agenerate_voting_samples(
    "list the cities in bay areas",
    num_samples=5,
    temperature=0.7
    # use_cache=False by default for voting
)

# Perform majority voting
from collections import Counter
answers = [s.content for s in samples]
most_common = Counter(answers).most_common(1)[0][0]
```

**Note:** Each voting sample gets a unique cache key to prevent race conditions. Caching voting samples is generally not recommended as it defeats the purpose of getting diverse responses.

### 5. Output Format Control

Control output format by passing `response_format` in kwargs. LiteLLM automatically handles provider-specific differences:

**JSON Mode (OpenAI):**

```python
result = await llm.agenerate(
    "Return a JSON object with key 'city' and value 'San Francisco'",
    response_format={"type": "json_object"}
)
# Note: OpenAI requires 'json' to appear in prompt/system message
```

**Structured Outputs (OpenAI):**

```python
schema = {
    "type": "object",
    "properties": {
        "answer": {"type": "integer"},
        "reasoning": {"type": "string"}
    },
    "required": ["answer", "reasoning"],
    "additionalProperties": False
}

result = await llm.agenerate(
    "What is 15 * 23?",
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "response",
            "schema": schema,
            "strict": True
        }
    }
)
```

**Note:** LiteLLM automatically handles `response_format` according to each provider's API. For OpenAI, JSON mode requires "json" in the prompt/system message. Structured outputs don't require this.

### 6. Metrics Tracking

```python
result = await llm.agenerate("What is Python?")
print(f"Tokens: {result.metrics.total_tokens}")
print(f"Cost: ${result.metrics.cost:.6f}")
print(f"Duration: {result.metrics.call_duration:.2f}s")
print(f"Cache read tokens: {result.metrics.cache_read_tokens}")
```

**Aggregate metrics:**

```python
from utils.llm_utils import aggregate_responses

results = await llm.agenerate_batch(prompts)
stats = aggregate_responses(results)
print(f"Total cost: ${stats['total_cost']:.6f}")
print(f"Cache hit rate: {stats['cache_hit_rate']:.1f}%")
```

### 7. Multiple Providers

**OpenAI:**

```python
llm = LLMClient(model="gpt-4o-mini")
```

**Fireworks:**

```python
llm = LLMClient(model="fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct")
```

**Anthropic:**

```python
llm = LLMClient(model="anthropic/claude-3-5-sonnet")
```

**Ollama (local):**

```python
llm = LLMClient(model="ollama/llama3")
```

### 8. Configuration

**Environment Variables:**

```bash
export LLM_UTILS_DEFAULT_MODEL="gpt-4o-mini"
export LLM_UTILS_CACHE_DIR="./cache"
export LLM_UTILS_MAX_CONCURRENT=32
export LLM_UTILS_MAX_RETRIES=5
export LLM_UTILS_MAX_REQUESTS_PER_MINUTE=600
```

**Direct Parameters:**

```python
llm = LLMClient(
    model="gpt-4o-mini",
    cache_dir="./cache",
    max_concurrent=32,
    max_retries=5,
    max_requests_per_minute=600,
    enable_retry=True
)
```

### 9. API Key Configuration

**Environment Variables (recommended):**

```bash
export OPENAI_API_KEY="your-key"
export FIREWORKS_API_KEY="your-key"
export ANTHROPIC_API_KEY="your-key"
```

**Explicit Parameters:**

```python
llm = LLMClient(
    model="gpt-4o-mini",
    api_key="your-key",
    organization="your-org"  # Optional, for OpenAI
)
```

## Complete Examples

### Example 1: Batch Processing with Metrics

```python
import asyncio
from utils.llm_utils import LLMClient, aggregate_responses

async def main():
    llm = LLMClient(model="gpt-4o-mini", max_concurrent=32)
    
    prompts = [
        "list the cities in bay areas",
        "list the cities in new york metropolitan area",
        "list the cities in los angeles metropolitan area"
    ]
    
    results = await llm.agenerate_batch(prompts, show_progress=True)
    stats = aggregate_responses(results)
    
    print(f"Total cost: ${stats['total_cost']:.6f}")
    print(f"Total tokens: {stats['total_tokens']}")
    print(f"Cache hit rate: {stats['cache_hit_rate']:.1f}%")
    
    for i, result in enumerate(results):
        print(f"\n[{i+1}] {result.content[:100]}...")

asyncio.run(main())
```

### Example 2: Voting/Self-Consistency

```python
import asyncio
from collections import Counter
from utils.llm_utils import LLMClient

async def main():
    llm = LLMClient(model="gpt-4o-mini")
    
    # Generate 5 diverse samples
    samples = await llm.agenerate_voting_samples(
        "list the cities in bay areas",
        num_samples=5,
        temperature=0.7
    )
    
    # Perform majority voting
    answers = [s.content.strip() for s in samples]
    vote_counts = Counter(answers)
    most_common = vote_counts.most_common(1)[0]
    
    print(f"Most common answer: '{most_common[0]}'")
    print(f"Agreement: {most_common[1]}/5 samples")
    
    # Cost analysis
    total_cost = sum(s.metrics.cost for s in samples)
    print(f"Total cost: ${total_cost:.6f}")

asyncio.run(main())
```

### Example 3: Multiple Providers

```python
import asyncio
from utils.llm_utils import LLMClient

async def main():
    prompt = "Explain quantum computing in one sentence."
    
    # OpenAI
    llm_openai = LLMClient(model="gpt-4o-mini")
    result_openai = await llm_openai.agenerate(prompt)
    
    # Fireworks
    llm_fireworks = LLMClient(
        model="fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct"
    )
    result_fireworks = await llm_fireworks.agenerate(prompt)
    
    print("OpenAI:", result_openai.content)
    print("Fireworks:", result_fireworks.content)

asyncio.run(main())
```

### Example 4: Output Format Control

```python
import asyncio
import json
from utils.llm_utils import LLMClient

async def main():
    llm = LLMClient(model="gpt-4o-mini")
    
    # JSON Mode
    result = await llm.agenerate(
        "Return a JSON object with key 'cities' containing a list of 3 cities in the Bay Area",
        response_format={"type": "json_object"}
    )
    
    data = json.loads(result.content)
    print(f"Cities: {data['cities']}")
    
    # Structured Outputs (OpenAI)
    schema = {
        "type": "object",
        "properties": {
            "cities": {"type": "array", "items": {"type": "string"}},
            "count": {"type": "integer"}
        },
        "required": ["cities", "count"],
        "additionalProperties": False
    }
    
    result2 = await llm.agenerate(
        "List 3 cities in the Bay Area",
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": schema,
                "strict": True
            }
        }
    )
    
    data2 = json.loads(result2.content)
    print(f"Found {data2['count']} cities: {data2['cities']}")

asyncio.run(main())
```

### Example 5: Cache Management

```python
import asyncio
from utils.llm_utils import LLMClient

async def main():
    llm = LLMClient(model="gpt-4o-mini")
    prompt = "What is caching?"
    
    # First call - hits API
    result1 = await llm.agenerate(prompt)
    print(f"First call: Cached={result1.cached}, Cost=${result1.metrics.cost:.6f}")
    
    # Second call - uses cache
    result2 = await llm.agenerate(prompt)
    print(f"Second call: Cached={result2.cached}, Cost=${result2.metrics.cost:.6f}")
    
    # Third call with refresh - hits API again
    result3 = await llm.agenerate(prompt, cache_refresh=True)
    print(f"Third call (refresh): Cached={result3.cached}, Cost=${result3.metrics.cost:.6f}")

asyncio.run(main())
```

## API Reference

### LLMClient

**Initialization:**

```python
LLMClient(
    model: Optional[str] = None,
    cache_dir: Optional[str] = None,
    max_concurrent: Optional[int] = None,
    max_retries: Optional[int] = None,
    max_requests_per_minute: Optional[int] = None,
    enable_retry: bool = True,
    api_key: Optional[str] = None,
    organization: Optional[str] = None
)
```

**Methods:**

- `agenerate(prompt, system="", temperature, use_cache, cache_refresh, **kwargs)` - Async generation
  - `system`: System message (default empty string)
  - `**kwargs`: Additional parameters passed to litellm (e.g., `max_tokens`, `seed`, `response_format`)
- `generate(prompt, system="", temperature, use_cache, cache_refresh, **kwargs)` - Sync generation
- `agenerate_batch(prompts, system="", temperature, use_cache, cache_refresh, show_progress, **kwargs)` - Async batch
- `generate_batch(prompts, **kwargs)` - Sync batch
- `agenerate_voting_samples(prompt, num_samples, system="", temperature, use_cache=False, **kwargs)` - Voting samples (async)
  - `use_cache`: Default `False` for voting to ensure diversity
- `generate_voting_samples(prompt, num_samples, **kwargs)` - Voting samples (sync)

### LLMResponse

```python
@dataclass
class LLMResponse:
    content: str
    metrics: LLMMetrics
    model_used: str
    cached: bool = False
    raw_response: Dict[str, Any] = field(default_factory=dict)
```

### LLMMetrics

```python
@dataclass
class LLMMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    call_duration: float = 0.0
```

### Utility Functions

- `sum_metrics(metrics_list: List[LLMMetrics]) -> LLMMetrics` - Aggregate metrics
- `aggregate_responses(responses: List[LLMResponse]) -> Dict[str, Any]` - Aggregate statistics

## Notes

- **Caching**: Responses are cached based on model, messages, and all kwargs (temperature, seed, response_format, etc.)
  - Only successful responses are cached. Errors (rate limits, connection failures, etc.) are never cached, so future calls will retry the API.
  - Voting samples get unique cache keys to prevent race conditions when caching is enabled
- **Voting**: `agenerate_voting_samples()` defaults to `use_cache=False` to ensure diversity. Each sample gets a unique cache key if caching is enabled.
- **System Prompt**: Defaults to empty string (`""`). Pass a system message explicitly if needed.
- **Output Format**: Control output format via `response_format` in kwargs. LiteLLM handles provider-specific differences automatically.
  - JSON Mode: `response_format={"type": "json_object"}` (OpenAI requires "json" in prompt/system)
  - Structured Outputs: `response_format={"type": "json_schema", "json_schema": {...}}` (OpenAI)
- **Concurrency**: `max_concurrent` limits parallel requests to prevent rate limits
- **Rate Limiting**: `max_requests_per_minute` enforces per-minute rate limits
- **Retries**: Automatic retries with exponential backoff for transient errors (rate limits, connection errors, service unavailable)
  - Retry decorator is cached once per instance for efficiency
  - If all retries fail, the exception is raised and nothing is cached
- **Token Counting**: Automatic fallback to manual token counting for local models (Ollama/vLLM) that don't return usage stats
- **Cost Calculation**: Uses LiteLLM's cost calculation with safe fallback for unknown models

## Examples

See the `examples/` directory for additional usage examples:

- `voting_example.py` - Voting/self-consistency patterns
- `multi_provider_example.py` - Using multiple providers
- `multiple_baselines.py` - Running multiple independent baselines
