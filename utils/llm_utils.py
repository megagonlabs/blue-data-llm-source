"""
LLM Client Utilities

A comprehensive LLM client with:
- Async/Sync support with concurrency control
- Metrics tracking (tokens, cost, cache usage, duration)
- Persistent caching to reduce costs
- Automatic retries with exponential backoff
- Error logging
- Batch processing utilities

Required dependencies: pip install litellm tenacity diskcache
Optional dependency: pip install nest_asyncio (for Jupyter/active event loops)
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

# Core Dependencies: pip install litellm tenacity diskcache
import litellm
from diskcache import Cache
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

# Optional dependency for Jupyter/active event loops: pip install nest_asyncio
try:
    import nest_asyncio
    _NEST_ASYNCIO_AVAILABLE = True
except ImportError:
    _NEST_ASYNCIO_AVAILABLE = False

# --- Configuration (with environment variable support) ---
# Default cache directory: cache/ in repo root
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.getenv("LLM_UTILS_CACHE_DIR", os.path.join(_repo_root, "cache"))
DEFAULT_MODEL = os.getenv("LLM_UTILS_DEFAULT_MODEL", "gpt-4o")
DEFAULT_TEMP = float(os.getenv("LLM_UTILS_DEFAULT_TEMP", "0.0"))
DEFAULT_MAX_CONCURRENT = int(os.getenv("LLM_UTILS_MAX_CONCURRENT", ""))
DEFAULT_MAX_RETRIES = int(os.getenv("LLM_UTILS_MAX_RETRIES", "5"))
DEFAULT_MAX_REQUESTS_PER_MINUTE = int(os.getenv("LLM_UTILS_MAX_REQUESTS_PER_MINUTE", "240"))

# Configure Logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("LLM_Utils")

# Suppress noisy LiteLLM logs
litellm.suppress_instrumentation = True


@dataclass
class LLMMetrics:
    """
    Metrics for LLM API calls.
    
    Tracks token usage, costs, cache efficiency, and timing.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    call_duration: float = 0.0  # Duration in seconds
    
    def __add__(self, other: "LLMMetrics") -> "LLMMetrics":
        """Aggregate metrics for batch analysis."""
        return LLMMetrics(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost=self.cost + other.cost,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens + other.cache_creation_tokens,
            call_duration=self.call_duration + other.call_duration,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost": self.cost,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "call_duration": self.call_duration,
        }


@dataclass
class LLMResponse:
    """
    Response object from LLM API calls.
    
    Contains the generated content, metrics, and metadata.
    """
    content: str
    metrics: LLMMetrics
    model_used: str
    cached: bool = False
    raw_response: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "content": self.content,
            "metrics": self.metrics.to_dict(),
            "model_used": self.model_used,
            "cached": self.cached,
        }


class LLMClient:
    """
    LLM client with async/sync support, caching, and metrics.
    
    Features:
    - Async/Sync Support: Choose async for batch processing or sync for simple scripts
    - Concurrency Control: Limit concurrent requests to prevent rate limits
    - Persistent Caching: Disk-based caching to reduce API costs
    - Automatic Retries: Exponential backoff for transient errors
    - Metrics Tracking: Track tokens, costs, cache usage, and timing
    - Error Logging: Detailed logging for debugging
    
    Example:
        # Async batch processing
        llm = LLMClient(model="gpt-4o-mini", max_concurrent=10)
        results = await asyncio.gather(*[llm.agenerate(p) for p in prompts])
        
        # Sync for simple scripts
        llm = LLMClient(model="gpt-4o")
        result = llm.generate("Explain quantum computing")
    """

    def __init__(
        self,
        model: Optional[str] = None,
        cache_dir: Optional[str] = None,
        max_concurrent: Optional[int] = None,
        max_retries: Optional[int] = None,
        max_requests_per_minute: Optional[int] = None,
        enable_retry: bool = True,
        # API Key Configuration (optional - defaults to environment variables)
        api_key: Optional[str] = None,
        organization: Optional[str] = None,
    ):
        """
        Initialize the LLMClient.
        
        All parameters can be set via environment variables (with LLM_UTILS_ prefix)
        or passed directly. Direct parameters override environment variables.
        
        Args:
            model: Model identifier (e.g., "gpt-4o", "anthropic/claude-3-5-sonnet")
                Default: LLM_UTILS_DEFAULT_MODEL env var or "gpt-4o"
            cache_dir: Directory for persistent caching
                Default: LLM_UTILS_CACHE_DIR env var or ".cache_llm"
            max_concurrent: Maximum concurrent async requests (prevents rate limits)
                Default: LLM_UTILS_MAX_CONCURRENT env var or 20
            max_retries: Maximum number of retry attempts
                Default: LLM_UTILS_MAX_RETRIES env var or 5
            max_requests_per_minute: Maximum requests per minute (rate limiting)
                Default: LLM_UTILS_MAX_REQUESTS_PER_MINUTE env var or 600
                Set to None to disable per-minute rate limiting
            enable_retry: Whether to enable automatic retries
            api_key: Optional API key. If None, LiteLLM uses environment variables.
                Pass directly to avoid setting env vars. LiteLLM handles provider detection.
            organization: Optional organization ID (for OpenAI and other providers)
        """
        # Use provided values or fall back to environment variables or defaults
        self.model = model or DEFAULT_MODEL
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache = Cache(self.cache_dir)
        self.max_concurrent = max_concurrent or DEFAULT_MAX_CONCURRENT
        self.semaphore = asyncio.Semaphore(self.max_concurrent)
        self.max_retries = max_retries or DEFAULT_MAX_RETRIES
        self.enable_retry = enable_retry
        
        # Per-minute rate limiting
        self.max_requests_per_minute = max_requests_per_minute
        if self.max_requests_per_minute is None:
            self.max_requests_per_minute = DEFAULT_MAX_REQUESTS_PER_MINUTE
        
        # Rate limiting state
        self._rate_limit_times: List[float] = []  # Track request times for rate limiting
        self._rate_limit_lock = asyncio.Lock()
        
        # Store API key and organization for passing to litellm calls
        # LiteLLM will handle provider detection automatically
        self.api_key = api_key
        self.organization = organization
        
        logger.info(
            f"Initialized LLMClient: model={self.model}, max_concurrent={self.max_concurrent}, "
            f"max_requests_per_minute={self.max_requests_per_minute}, cache_dir={self.cache_dir}, "
            f"retry_enabled={enable_retry}"
        )

    def _get_cache_key(self, messages: List[Dict], **kwargs) -> str:
        """Create a deterministic hash of input + params for caching."""
        key_data = {
            "model": self.model,
            "messages": messages,
            "kwargs": kwargs
        }
        # Sort keys to ensure consistent JSON string
        return hashlib.sha256(
            json.dumps(key_data, sort_keys=True).encode()
        ).hexdigest()

    def _extract_metrics(self, response_obj, duration: float = 0.0) -> LLMMetrics:
        """
        Extract standardized metrics from API response.
        
        Handles provider-specific differences (OpenAI vs Anthropic cache reporting).
        """
        usage = getattr(response_obj, "usage", None)
        if not usage:
            logger.warning("No usage information in response")
            return LLMMetrics(call_duration=duration)

        # Calculate cost using LiteLLM - try completion_cost first, fallback to cost_per_token
        cost = 0.0
        try:
            # Primary method: completion_cost (works with full response object)
            cost = litellm.completion_cost(completion_response=response_obj)
        except Exception:
            try:
                # Fallback: cost_per_token (more accurate, requires token counts)
                input_cost, output_cost = litellm.cost_per_token(
                    model=self.model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                )
                cost = input_cost + output_cost
            except Exception as e:
                logger.warning(f"Could not calculate cost: {e}")
                cost = 0.0

        # Handle provider differences in Cache reporting
        # OpenAI uses prompt_tokens_details.cached_tokens
        # Anthropic uses cache_creation_input_tokens / cache_read_input_tokens
        cache_read = 0
        cache_create = 0

        # Check OpenAI style
        if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
            cache_read = getattr(usage.prompt_tokens_details, "cached_tokens", 0)

        # Check Anthropic style (LiteLLM normalizes this mostly, but just in case)
        if hasattr(usage, "cache_read_input_tokens"):
            cache_read = usage.cache_read_input_tokens
        if hasattr(usage, "cache_creation_input_tokens"):
            cache_create = usage.cache_creation_input_tokens

        return LLMMetrics(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost=cost,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_create,
            call_duration=duration
        )

    def _create_retry_decorator(self):
        """Create retry decorator with current configuration."""
        if not self.enable_retry:
            # Return a no-op decorator
            def no_retry(func):
                return func
            return no_retry

        return retry(
            retry=retry_if_exception_type((
                litellm.APIConnectionError,
                litellm.RateLimitError,
                litellm.ServiceUnavailableError,
                litellm.APIError,
            )),
            wait=wait_exponential(multiplier=1, min=2, max=60),
            stop=stop_after_attempt(self.max_retries),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    @property
    def _retry_decorator(self):
        """Lazy property to create retry decorator."""
        return self._create_retry_decorator()

    async def _wait_for_rate_limit(self):
        """Wait if we've exceeded the per-minute rate limit."""
        if self.max_requests_per_minute is None:
            return
        
        async with self._rate_limit_lock:
            now = time.time()
            # Remove requests older than 1 minute
            self._rate_limit_times = [t for t in self._rate_limit_times if now - t < 60]
            
            # If we're at the limit, wait until the oldest request is > 1 minute old
            if len(self._rate_limit_times) >= self.max_requests_per_minute:
                oldest_time = min(self._rate_limit_times)
                wait_time = 60 - (now - oldest_time) + 0.1  # Add small buffer
                if wait_time > 0:
                    logger.debug(f"Rate limit reached, waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)
                    # Clean up again after waiting
                    now = time.time()
                    self._rate_limit_times = [t for t in self._rate_limit_times if now - t < 60]
            
            # Record this request
            self._rate_limit_times.append(now)

    async def _acall_api(self, messages: List[Dict], **kwargs):
        """
        Raw async API call protected by semaphore, rate limiting, and retries.
        
        This is the core async method that handles concurrency, rate limiting, and retries.
        """
        retry_decorator = self._create_retry_decorator()

        @retry_decorator
        async def _make_request():
            # Wait for rate limit if needed
            await self._wait_for_rate_limit()
            
            async with self.semaphore:
                try:
                    # Pass API key and organization if provided
                    call_kwargs = kwargs.copy()
                    if self.api_key is not None:
                        call_kwargs["api_key"] = self.api_key
                    if self.organization is not None:
                        call_kwargs["organization"] = self.organization
                    
                    return await litellm.acompletion(
                        model=self.model,
                        messages=messages,
                        **call_kwargs
                    )
                except Exception as e:
                    logger.error(
                        f"API call failed: {type(e).__name__}: {e}",
                        exc_info=True
                    )
                    raise

        return await _make_request()

    def _call_api_sync(self, messages: List[Dict], **kwargs):
        """
        Sync version of the API call with retry logic.
        
        Note: Sync calls don't benefit from semaphore-based concurrency control.
        For batch processing, use async methods instead.
        """
        retry_decorator = self._create_retry_decorator()

        @retry_decorator
        def _make_request():
            try:
                # Pass API key and organization if provided
                call_kwargs = kwargs.copy()
                if self.api_key is not None:
                    call_kwargs["api_key"] = self.api_key
                if self.organization is not None:
                    call_kwargs["organization"] = self.organization
                
                return litellm.completion(
                    model=self.model,
                    messages=messages,
                    **call_kwargs
                )
            except Exception as e:
                logger.error(
                    f"Sync API call failed: {type(e).__name__}: {e}",
                    exc_info=True
                )
                raise

        return _make_request()

    async def agenerate(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        temperature: float = DEFAULT_TEMP,
        use_cache: bool = True,
        cache_refresh: bool = False,
        **kwargs
    ) -> LLMResponse:
        """
        Async generation.
        
        Args:
            prompt: User prompt text
            system: System message (default helpful assistant)
            temperature: Sampling temperature (0.0 = deterministic)
            use_cache: Whether to use disk cache
            cache_refresh: If True, bypass cache and refresh the entry (useful for experiments)
            **kwargs: Additional parameters passed to litellm (e.g., max_tokens, seed)
        
        Returns:
            LLMResponse with content, metrics, and metadata
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ]

        cache_key = self._get_cache_key(messages, temperature=temperature, **kwargs)

        # Check cache (skip if cache_refresh is True)
        if use_cache and not cache_refresh and cache_key in self.cache:
            logger.debug(f"Cache hit: {cache_key[:8]}")
            cached_data = self.cache[cache_key]
            # Reconstruct object from cached dict
            # Ensure metrics is an LLMMetrics object, not a dict
            if isinstance(cached_data.get("metrics"), dict):
                cached_data["metrics"] = LLMMetrics(**cached_data["metrics"])
            elif not isinstance(cached_data.get("metrics"), LLMMetrics):
                # Fallback: create empty metrics if missing
                cached_data["metrics"] = LLMMetrics()
            return LLMResponse(**cached_data)
        
        if cache_refresh and use_cache:
            logger.debug(f"Cache refresh requested, bypassing cache for: {cache_key[:8]}")

        # Call API with timing
        start_time = time.time()
        try:
            response = await self._acall_api(
                messages,
                temperature=temperature,
                **kwargs
            )
            duration = time.time() - start_time

            # Extract content
            content = response.choices[0].message.content
            if content is None:
                logger.warning("Received None content from API")
                content = ""

            # Format Result
            result = LLMResponse(
                content=content,
                metrics=self._extract_metrics(response, duration=duration),
                model_used=self.model,
                cached=False,
                raw_response=dict(response) if hasattr(response, "__dict__") else {}
            )

            # Save to Disk
            if use_cache:
                # Cache as dict for easier serialization
                self.cache[cache_key] = {
                    "content": result.content,
                    "metrics": result.metrics.to_dict(),
                    "model_used": result.model_used,
                    "cached": True,
                    "raw_response": result.raw_response,
                }

            logger.debug(
                f"Generated response: {len(content)} chars, "
                f"{result.metrics.total_tokens} tokens, ${result.metrics.cost:.6f}"
            )

            return result

        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                f"Failed to generate after {duration:.2f}s: {type(e).__name__}: {e}",
                exc_info=True
            )
            raise

    def generate(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        temperature: float = DEFAULT_TEMP,
        use_cache: bool = True,
        cache_refresh: bool = False,
        **kwargs
    ) -> LLMResponse:
        """
        Synchronous wrapper for easy debugging or scripts that don't need async.
        
        For batch processing, prefer agenerate() with asyncio.gather().
        
        Args:
            prompt: User prompt text
            system: System message (default helpful assistant)
            temperature: Sampling temperature (0.0 = deterministic)
            use_cache: Whether to use disk cache
            cache_refresh: If True, bypass cache and refresh the entry
            **kwargs: Additional parameters passed to litellm (e.g., max_tokens, seed)
        
        Returns:
            LLMResponse with content, metrics, and metadata
        """
        # Handle event loop for sync calls
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # If we are already in an async loop (e.g. Jupyter), use nest_asyncio
            if _NEST_ASYNCIO_AVAILABLE:
                nest_asyncio.apply()
            else:
                logger.warning(
                    "nest_asyncio not installed. Install with: pip install nest_asyncio"
                )
                raise RuntimeError(
                    "Cannot run sync generate() in an active event loop. "
                    "Use agenerate() instead or install nest_asyncio."
                )

        return loop.run_until_complete(
            self.agenerate(
                prompt,
                system=system,
                temperature=temperature,
                use_cache=use_cache,
                cache_refresh=cache_refresh,
                **kwargs
            )
        )

    async def agenerate_batch(
        self,
        prompts: List[str],
        system: str = "You are a helpful assistant.",
        temperature: float = DEFAULT_TEMP,
        use_cache: bool = True,
        cache_refresh: bool = False,
        show_progress: bool = False,
        **kwargs
    ) -> List[LLMResponse]:
        """
        Batch async generation.
        
        Args:
            prompts: List of prompts to process
            system: System message for all prompts
            temperature: Sampling temperature
            use_cache: Whether to use disk cache
            cache_refresh: If True, bypass cache and refresh entries
            show_progress: Whether to log progress (useful for long batches)
            **kwargs: Additional parameters passed to litellm
        
        Returns:
            List of LLMResponse objects in the same order as prompts
        """
        if show_progress:
            logger.info(f"Starting batch generation: {len(prompts)} prompts")

        tasks = [
            self.agenerate(
                prompt=p,
                system=system,
                temperature=temperature,
                use_cache=use_cache,
                cache_refresh=cache_refresh,
                **kwargs
            )
            for p in prompts
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle exceptions in results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"Error processing prompt {i}: {type(result).__name__}: {result}",
                    exc_info=True
                )
                # Create error response
                error_response = LLMResponse(
                    content="",
                    metrics=LLMMetrics(),
                    model_used=self.model,
                    cached=False,
                )
                processed_results.append(error_response)
            else:
                processed_results.append(result)

        if show_progress:
            total_metrics = sum_metrics([r.metrics for r in processed_results])
            logger.info(
                f"Batch complete: {len(processed_results)} responses, "
                f"{total_metrics.total_tokens} tokens, ${total_metrics.cost:.6f}"
            )

        return processed_results

    def generate_batch(
        self,
        prompts: List[str],
        **kwargs
    ) -> List[LLMResponse]:
        """
        Synchronous batch generation wrapper.
        
        For large batches, prefer agenerate_batch() for better performance.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            if _NEST_ASYNCIO_AVAILABLE:
                nest_asyncio.apply()
            else:
                raise RuntimeError(
                    "Cannot run sync generate_batch() in an active event loop. "
                    "Use agenerate_batch() instead or install nest_asyncio."
                )

        return loop.run_until_complete(self.agenerate_batch(prompts, **kwargs))

    async def agenerate_voting_samples(
        self,
        prompt: str,
        num_samples: int = 5,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.7,
        use_cache: bool = True,
        **kwargs
    ) -> List[LLMResponse]:
        """
        Generate multiple diverse responses for voting/self-consistency.
        
        This method generates multiple different responses from the same prompt
        for voting. It uses cache_refresh=True to ensure diversity.
        
        Args:
            prompt: User prompt text
            num_samples: Number of diverse samples to generate
            system: System message
            temperature: Sampling temperature (should be > 0 for diversity)
            use_cache: Whether to use disk cache (cache_refresh=True ensures fresh responses)
            **kwargs: Additional parameters passed to litellm
        
        Returns:
            List of LLMResponse objects (diverse responses for voting)
        
        Example:
            llm = LLMClient(model="gpt-4o-mini")
            samples = await llm.agenerate_voting_samples(
                "What is 2+2?",
                num_samples=5,
                temperature=0.7
            )
            # Perform majority voting
            answers = [s.content for s in samples]
        """
        if temperature == 0.0:
            logger.warning(
                "Temperature is 0.0 - responses will be deterministic. "
                "Consider using temperature > 0 for voting diversity."
            )
        
        # Generate samples with cache_refresh to ensure diversity
        tasks = [
            self.agenerate(
                prompt=prompt,
                system=system,
                temperature=temperature,
                use_cache=use_cache,
                cache_refresh=True,  # Force fresh response for each sample
                **kwargs
            )
            for _ in range(num_samples)
        ]
        
        return await asyncio.gather(*tasks)

    def generate_voting_samples(
        self,
        prompt: str,
        num_samples: int = 5,
        **kwargs
    ) -> List[LLMResponse]:
        """
        Synchronous wrapper for generate_voting_samples.
        
        See agenerate_voting_samples() for details.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            if _NEST_ASYNCIO_AVAILABLE:
                nest_asyncio.apply()
            else:
                raise RuntimeError(
                    "Cannot run sync generate_voting_samples() in an active event loop. "
                    "Use agenerate_voting_samples() instead or install nest_asyncio."
                )

        return loop.run_until_complete(
            self.agenerate_voting_samples(prompt, num_samples=num_samples, **kwargs)
        )


# --- Utility Functions ---

def sum_metrics(metrics_list: List[LLMMetrics]) -> LLMMetrics:
    """
    Aggregate metrics from multiple LLM calls.
    
    Example:
        results = await llm.agenerate_batch(prompts)
        total = sum_metrics([r.metrics for r in results])
        print(f"Total cost: ${total.cost:.2f}")
    """
    if not metrics_list:
        return LLMMetrics()

    # Ensure all items are LLMMetrics objects
    normalized_list = []
    for m in metrics_list:
        if isinstance(m, dict):
            normalized_list.append(LLMMetrics(**m))
        elif isinstance(m, LLMMetrics):
            normalized_list.append(m)
        else:
            # Fallback to empty metrics
            normalized_list.append(LLMMetrics())

    total = normalized_list[0]
    for m in normalized_list[1:]:
        total = total + m
    return total


def aggregate_responses(responses: List[LLMResponse]) -> Dict[str, Any]:
    """
    Aggregate statistics from a batch of responses.
    
    Returns a dictionary with:
    - total_cost: Sum of all costs
    - total_tokens: Sum of all tokens
    - cache_hit_rate: Percentage of cached responses
    - avg_duration: Average call duration
    - cache_efficiency: Percentage of tokens from cache
    """
    if not responses:
        return {
            "total_cost": 0.0,
            "total_tokens": 0,
            "cache_hit_rate": 0.0,
            "avg_duration": 0.0,
            "cache_efficiency": 0.0,
        }

    # Normalize metrics to LLMMetrics objects
    normalized_metrics = []
    for r in responses:
        if isinstance(r.metrics, dict):
            normalized_metrics.append(LLMMetrics(**r.metrics))
        elif isinstance(r.metrics, LLMMetrics):
            normalized_metrics.append(r.metrics)
        else:
            normalized_metrics.append(LLMMetrics())
    
    total_metrics = sum_metrics(normalized_metrics)
    cache_hits = sum(1 for r in responses if r.cached)
    total_input_tokens = sum(m.input_tokens for m in normalized_metrics)
    total_cache_read = sum(m.cache_read_tokens for m in normalized_metrics)

    return {
        "total_cost": total_metrics.cost,
        "total_tokens": total_metrics.total_tokens,
        "cache_hit_rate": (cache_hits / len(responses)) * 100 if responses else 0.0,
        "avg_duration": total_metrics.call_duration / len(responses) if responses else 0.0,
        "cache_efficiency": (total_cache_read / total_input_tokens * 100) if total_input_tokens > 0 else 0.0,
        "total_input_tokens": total_metrics.input_tokens,
        "total_output_tokens": total_metrics.output_tokens,
        "cache_read_tokens": total_metrics.cache_read_tokens,
        "cache_creation_tokens": total_metrics.cache_creation_tokens,
    }


# --- Example Usage ---
async def main():
    """
    Example workflow demonstrating batch processing and metrics.
    """
    # 1. Initialize (Points to OpenAI by default, or "ollama/llama3", "anthropic/claude-3-5-sonnet")
    llm = LLMClient(model="gpt-4o-mini", max_concurrent=10)

    # 2. Define Dataset
    prompts = [f"Explain concept {i} in 1 sentence." for i in range(5)]

    # 3. Run Parallel Evaluation
    logger.info("Starting batch evaluation...")
    results = await llm.agenerate_batch(prompts, temperature=0.7, show_progress=True)

    # 4. Analyze Results
    stats = aggregate_responses(results)
    
    print("\n--- Batch Results ---")
    print(f"Total Cost: ${stats['total_cost']:.6f}")
    print(f"Total Tokens: {stats['total_tokens']}")
    print(f"Cache Hit Rate: {stats['cache_hit_rate']:.1f}%")
    print(f"Average Duration: {stats['avg_duration']:.2f}s")
    print(f"Cache Efficiency: {stats['cache_efficiency']:.1f}%")
    
    print("\n--- Individual Results ---")
    for i, (p, r) in enumerate(zip(prompts, results)):
        print(f"\n[{i+1}] Prompt: {p[:30]}...")
        print(f"    Cache Hit: {r.cached} | Cost: ${r.metrics.cost:.6f}")
        print(f"    Tokens: {r.metrics.total_tokens} | Duration: {r.metrics.call_duration:.2f}s")
        print(f"    Output: {r.content}")


if __name__ == "__main__":
    # pip install nest_asyncio if running in Jupyter
    asyncio.run(main())
