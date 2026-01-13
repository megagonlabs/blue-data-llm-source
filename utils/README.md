# LLM Client Utilities

A comprehensive LLM client with async/sync support, caching, metrics tracking, and batch processing.

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

Generate multiple diverse responses for voting:

```python
samples = await llm.agenerate_voting_samples(
    "list the cities in bay areas",
    num_samples=5,
    temperature=0.7
)

# Perform majority voting
from collections import Counter
answers = [s.content for s in samples]
most_common = Counter(answers).most_common(1)[0][0]
```

### 5. Metrics Tracking

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

### 6. Multiple Providers

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

### 7. Configuration

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

### 8. API Key Configuration

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

### Example 4: Cache Management

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

- `agenerate(prompt, system, temperature, use_cache, cache_refresh, **kwargs)` - Async generation
- `generate(prompt, system, temperature, use_cache, cache_refresh, **kwargs)` - Sync generation
- `agenerate_batch(prompts, system, temperature, use_cache, cache_refresh, show_progress, **kwargs)` - Async batch
- `generate_batch(prompts, **kwargs)` - Sync batch
- `agenerate_voting_samples(prompt, num_samples, system, temperature, use_cache, **kwargs)` - Voting samples (async)
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

- **Caching**: Responses are cached based on model, messages, and all kwargs (temperature, seed, etc.)
  - Only successful responses are cached. Errors (rate limits, connection failures, etc.) are never cached, so future calls will retry the API.
- **Voting**: Use `cache_refresh=True` or `agenerate_voting_samples()` to ensure diversity
- **Concurrency**: `max_concurrent` limits parallel requests to prevent rate limits
- **Rate Limiting**: `max_requests_per_minute` enforces per-minute rate limits
- **Retries**: Automatic retries with exponential backoff for transient errors (rate limits, connection errors, service unavailable)
  - If all retries fail, the exception is raised and nothing is cached

- **Cost Calculation**: Uses LiteLLM's cost calculation for accurate pricing

## See Also

- `CACHING_AND_VOTING.md` - Detailed guide on caching and voting patterns
- `examples/` - Additional usage examples
