import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
import asyncio
import hashlib
import time

from src.schema import SchemaDefinition, SchemaGenerator, Column
from src.analysis import DataTask, QueryAnalyzer, QueryAnalysisResult
from src.prompts import (
    DISTRIBUTION_ESTIMATION_PROMPT,
    PARTITION_VALUES_PROMPT,
    SUBQUERY_REWRITE_PROMPT
)
from utils.llm_utils import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class DistributionStats:
    key_name: str
    num_partitions: int
    max_partition_size: int
    ambiguity_score: float # 0-1, 1 is bad
    reasoning: str
    partition_counts: Dict[str, int] = field(default_factory=dict)

@dataclass
class StrategyCandidate:
    schema: SchemaDefinition
    partition_key: str
    score: float = 0.0
    stats: Optional[DistributionStats] = None
    reasoning: str = ""

class StrategyEvaluator:
    """
    Jointly evaluates (Schema, PartitionKey) candidates.
    """
    def __init__(self, llm_client: LLMClient):
        self.estimator = DistributionEstimator(llm_client)
        
    async def evaluate_concurrent(self, query: str, candidates: List[StrategyCandidate]) -> List[StrategyCandidate]:
        """
        Runs distribution estimation in parallel for all candidates.
        """
        async def evaluate_one(cand: StrategyCandidate) -> StrategyCandidate:
            stats = await self.estimator.estimate(query, cand.partition_key)
            cand.stats = stats
            # Simple Scoring Model
            # Penalty for Ambiguity (0.0 to 1.0) -> high ambiguity is bad
            # Penalty for too many partitions (>50)
            # Penalty for too few partitions (<2)
            # Penalty for fat partitions (>200 items?)
            
            score = 100.0
            score -= (stats.ambiguity_score * 50) # Max -50
            
            if stats.num_partitions > 50:
                score -= 30
            elif stats.num_partitions < 2:
                # If row count is high, low partition count is bad.
                # But we don't know total row count here easily without context.
                # Assuming decomposition is needed, 1 partition is useless.
                score -= 50
                
            # Prefer 5-20 partitions
            if 5 <= stats.num_partitions <= 20:
                score += 10
                
            cand.score = score
            cand.reasoning = stats.reasoning
            return cand

        # Run all
        tasks = [evaluate_one(c) for c in candidates]
        results = await asyncio.gather(*tasks)
        return results

    def select_best(self, candidates: List[StrategyCandidate]) -> Optional[StrategyCandidate]:
        if not candidates:
            return None
        # Sort by score descending
        sorted_cands = sorted(candidates, key=lambda x: x.score, reverse=True)
        return sorted_cands[0]

class DistributionEstimator:
    """
    Estimates the data distribution for a given partition key.
    Helps avoid "Fat Partitions" or "Dust" (too many small partitions).
    """
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def estimate(self, query: str, key_name: str) -> DistributionStats:
        prompt = DISTRIBUTION_ESTIMATION_PROMPT.format(query=query, key_name=key_name)
        response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            data = json.loads(content)
            
            partition_counts = data.get("partitions", {})
            # Auto-calculate stats from the detailed counts
            if partition_counts:
                num_partitions = len(partition_counts)
                # Ensure values are ints
                max_partition_size = max(int(v) for v in partition_counts.values()) if partition_counts else 0
            else:
                num_partitions = int(data.get("num_partitions", 0))
                max_partition_size = int(data.get("max_partition_size", 0))
            
            return DistributionStats(
                key_name=key_name,
                num_partitions=num_partitions,
                max_partition_size=max_partition_size,
                ambiguity_score=data.get("ambiguity_score", 0.5),
                reasoning=data.get("reasoning", ""),
                partition_counts=partition_counts
            )
        except Exception as e:
            logger.error(f"Distribution estimation failed for {key_name}: {e}")
            return DistributionStats(key_name, 0, 0, 1.0, "Error")


@dataclass
class DataPlanRequest:
    task: DataTask # The Root Task
    schema: SchemaDefinition
    map_tasks: List[DataTask] # V3: List of DataTasks (Leaf Nodes)
    validation_policy: str = "cross_consistency"

class DataPlanBuilder:
    """
    Constructs a DataPlan (Map-Reduce Plan) from a high-level query.
    1. Uses SchemaGenerator to infer structure.
    2. Explores partition values (e.g. "List all States").
    3. Generates MapTasks.
    """
    def __init__(
        self, 
        llm_client: LLMClient, 
        max_partitions: int = 50,
        num_candidates: int = 3,
        temperature: float = 0.7
    ):
        self.llm = llm_client
        self.max_partitions = max_partitions
        self.num_candidates = num_candidates
        self.temperature = temperature
        
        self.analyzer = QueryAnalyzer(llm_client)
        self.schema_gen = SchemaGenerator(llm_client)
        self.evaluator = StrategyEvaluator(llm_client)
        self.dist_estimator = DistributionEstimator(llm_client)
        
    async def create_plan(
        self, 
        query: str, 
        row_threshold: int = 100,
        decomposition_strategy: str = "rule_based", # or "llm_based"
        partitioning_method: str = "smart", # "greedy", "semantic", "smart" (New V3 default)
        subquery_style: str = "natural",
        max_chunks: int = 100,
        context: dict = None,
        schema: Optional[SchemaDefinition] = None,
        execution_mode: str = "batch" # [NEW]
    ) -> DataPlanRequest:
        
        context = context or {}
        self.max_chunks_limit = max_chunks # Store for recursive tracking
        self.current_chunk_count = 0
        
        # 0. Analyze Query & Decompose
        # If strict schema is provided, we might still want analysis for estimation/decomposition, 
        # but the query rewrite might need to be careful not to drift from the schema.
        analysis = await self.analyzer.analyze(
            query, 
            decomposition_strategy=decomposition_strategy,
            row_threshold=row_threshold
        )
        logger.info(f"Query Analysis: {analysis}")
        
        # Use the structured task
        # root_task = analysis.task or DataTask(query=analysis.rewritten_query)
        # FORCE FRESH TASK to ensure no stale state
        root_task = DataTask(
            query=analysis.rewritten_query,
            constraints=analysis.task.constraints if analysis.task else {},
            collection_strategy=analysis.task.collection_strategy if analysis.task else "ALL",
            execution_mode=execution_mode,
            estimated_rows=analysis.estimated_rows
        )
        root_task.schema = None # Will be set below
        
        rewritten_query = root_task.query
        final_schema = None
        best_strategy = None
        
        # [NEW] 1. Check for Strict Schema
        if schema:
            logger.info(f"Using provided strict Schema: {schema.table_name}")
            final_schema = schema
            # If a schema is provided, we skip Joint Optimization for the *schema* part.
            # However, we still might need to pick a partition key from this provided schema if decomposition is required.
            
            if analysis.requires_decomposition:
                # We need to find a strategy using the PROVIDED schema
                # We can reuse the evaluator to find the best key in this schema
                logger.info("Evaluating provided schema for best partition key...")
                
                strategy_candidates: List[StrategyCandidate] = []
                partition_keys = final_schema.get_partition_keys()
                
                for pk in partition_keys:
                    strategy_candidates.append(StrategyCandidate(
                        schema=final_schema,
                        partition_key=pk.name
                    ))
                
                if strategy_candidates:
                    # Evaluate concurrent
                    evaluated_candidates = await self.evaluator.evaluate_concurrent(rewritten_query, strategy_candidates)
                    best_strategy = self.evaluator.select_best(evaluated_candidates)
                    if best_strategy:
                        logger.info(f"Selected Best Partition Key for Strict Schema: {best_strategy.partition_key}")

        # 1. Joint Optimization (Schema + Partition Strategy) - ONLY IF NO SCHEMA PROVIDED
        elif partitioning_method == "smart" and analysis.requires_decomposition:
            logger.info("Running Joint Optimization (Smart Planner)...")
            
            # A. Generate Column Pool
            pool = await self.schema_gen.generate_pool(rewritten_query)
            
            # B. Generate Schema Candidates
            raw_candidates = await self.schema_gen.generate_candidates(rewritten_query, k=3, column_pool=pool)
            
            # C. Formulate Strategy Candidates
            strategy_candidates: List[StrategyCandidate] = []
            
            for raw_cand in raw_candidates:
                # Infer types/keys to find *potential* strategies for this schema
                schema_def = await self.schema_gen.infer_types_and_keys(rewritten_query, raw_cand)
                partition_keys = schema_def.get_partition_keys()
                
                if not partition_keys:
                    continue
                    
                for pk in partition_keys:
                    strategy_candidates.append(StrategyCandidate(
                        schema=schema_def,
                        partition_key=pk.name
                    ))
            
            if strategy_candidates:
                # D. Concurrent Evaluation
                evaluated_candidates = await self.evaluator.evaluate_concurrent(rewritten_query, strategy_candidates)
                
                # E. Selection
                best_strategy = self.evaluator.select_best(evaluated_candidates)
                if best_strategy:
                    final_schema = best_strategy.schema
                    logger.info(f"Selected Best Strategy: Schema={final_schema.table_name}, Key={best_strategy.partition_key}, Score={best_strategy.score}")
            else:
                logger.warning("No strategy candidates found. Falling back to simple schema inference.")

        # Fallback / Non-Smart Path
        if not final_schema:
            strategy = "first" if partitioning_method == "greedy" else "llm_rank"
            final_schema = await self.schema_gen.infer_schema(rewritten_query, strategy=strategy, context=context)
        
        # Attach schema to root task
        root_task.schema = final_schema
        
        map_tasks: List[DataTask] = []
        
        # Decide: Flat vs Partitioned
        if not analysis.requires_decomposition:
            logger.info("Decomposition not required. Creating single flat task.")
            flat_task = DataTask(
                query=rewritten_query,
                constraints=root_task.constraints,
                collection_strategy=root_task.collection_strategy,
                schema=final_schema,
                parent=root_task,
                partition_key="All",
                partition_value="All",
                execution_mode=root_task.execution_mode
            )
            map_tasks.append(flat_task)
        else:
            # Recursive Plan Generation
            pass_key = best_strategy.partition_key if best_strategy else None
            
            map_tasks = await self._create_plan_recursive(
                root_task, 
                final_schema, 
                depth=0, 
                partitioning_method=partitioning_method,
                row_threshold=row_threshold,
                max_chunks=max_chunks,
                suggested_key=pass_key # [NEW]
            )
            
        # Artifact Persistence
        if "output_dir" in context:
            import dataclasses
            import os
            try:
                plan_artifact = {
                    "query": query,
                    "rewritten_query": rewritten_query,
                    "schema": final_schema.to_dict(),
                    "map_tasks": [dataclasses.asdict(t) for t in map_tasks],
                    "decomposition_strategy": decomposition_strategy
                }
                with open(os.path.join(context["output_dir"], "plan.json"), "w") as f:
                    json.dump(plan_artifact, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to save plan artifact: {e}")
                
        return DataPlanRequest(
            task=root_task,
            schema=final_schema,
            map_tasks=map_tasks
        )

    async def _create_plan_recursive(
        self, 
        task: DataTask, 
        schema: SchemaDefinition, 
        depth: int = 0,
        max_depth: int = 3, # V3 Default
        partitioning_method: str = "smart",
        row_threshold: int = 100,
        max_chunks: int = 100,
        subquery_style: str = "natural",
        used_keys: Optional[set] = None,
        suggested_key: Optional[str] = None # [NEW] V3
    ) -> List[DataTask]:
        
        used_keys = used_keys or set()
        
        query = task.query
        logger.info(f"Recursive Planning (Depth {depth}): {query} (Method: {partitioning_method})")
        map_tasks: List[DataTask] = []
        
        # Check Global Chunk Limit
        if hasattr(self, 'current_chunk_count') and self.current_chunk_count >= getattr(self, 'max_chunks_limit', 100):
            logger.warning(f"Global max_chunks limit ({self.max_chunks_limit}) reached. Creating flat task.")
            return [DataTask(
                query=query,
                constraints=task.constraints,
                schema=schema,
                parent=task,
                partition_key="Filter",
                partition_value="Remaining"
            )]
        
        # If too deep, fallback to flat
        if depth > max_depth:
            logger.warning(f"Max depth {max_depth} reached. Creating flat task for: {query}")
            return [DataTask(
                query=query,
                constraints=task.constraints,
                schema=schema,
                parent=task,
                partition_key="Filter",
                partition_value="Remaining",
                execution_mode=getattr(task, 'execution_mode', 'batch'),
                estimated_rows=task.estimated_rows if depth == 0 else None
            )]
            
        # 2. Identify Assignments (Partition Strategy)
        partition_keys = []
        
        if suggested_key and depth == 0:
            # V3 Joint Optimization: Use pre-selected strategy
            candidates = [c for c in schema.columns if c.name == suggested_key]
            if candidates:
                partition_keys = candidates
                logger.info(f"Using Joint Optimization Suggested Key: {suggested_key}")
            else:
                logger.warning(f"Suggested key {suggested_key} not found in schema. Fallback to search.")
        
        if not partition_keys:
            # Standard Search
            partition_keys = [k for k in schema.get_partition_keys() if k.name not in used_keys]
        
        if not partition_keys:
            logger.info("No partition keys found or all used. Fallback to flat.")
            return [DataTask(
                query=query,
                constraints=task.constraints,
                schema=schema,
                parent=task,
                partition_key="All",
                partition_value="All",
                execution_mode=getattr(task, 'execution_mode', 'batch'),
                estimated_rows=task.estimated_rows if depth == 0 else None
            )]

        # Intelligent Selection (Phase 2.3)
        best_key = None
        best_stats = None
        
        # If we have a single forced key (suggested), skip evaluation
        if suggested_key and len(partition_keys) == 1:
            best_key = partition_keys[0]
            selected_stats = None
        elif partitioning_method == "greedy":
            best_key = partition_keys[0]
            selected_stats = None
        else:
            # SEMANTIC / SMART MODE (Local Evaluation)
            best_score = float('inf')
            
            for pkey in partition_keys:
                stats = await self.dist_estimator.estimate(query, pkey.name)
                # ... Simplified Scoring ...
                score = (stats.ambiguity_score * 1000)
                if stats.num_partitions > 50: score += 10000
                if stats.num_partitions < 2: score += 5000
                
                if best_stats is None or score < best_score:
                    best_score = score
                    best_key = pkey
                    best_stats = stats
            
            if best_stats and best_stats.ambiguity_score > 0.8:
                return [DataTask(
                    query=query,
                    constraints=task.constraints,
                    schema=schema,
                    parent=task,
                    partition_key="All",
                    partition_value="All",
                    estimated_rows=task.estimated_rows if depth == 0 else None
                )]
            selected_stats = best_stats

        pkey = best_key
        
        # 3. Fetch Values & Recurse
        if selected_stats and selected_stats.partition_counts:
            values = list(selected_stats.partition_counts.keys())
            counts = selected_stats.partition_counts
        else:
            values = await self._fetch_partition_values(query, pkey)
            counts = {}

        logger.info(f"Selected Key: {pkey.name}. Values: {len(values)}")

        # [FIX] Zero-Value Fallback: If no values generated (e.g. too many, or unknown), revert to flat task
        if not values:
            logger.warning(f"Partition key {pkey.name} generated NO values. Falling back to FLAT execution.")
            return [DataTask(
                query=query,
                constraints=task.constraints,
                collection_strategy=task.collection_strategy,
                schema=schema,
                parent=task,
                partition_key="All", # Fallback
                partition_value="Fallback",
                execution_mode=getattr(task, 'execution_mode', 'batch'),
                estimated_rows=task.estimated_rows
            )]

        # [NEW] Smart Quota Allocation Setup
        limit_n = None
        if task.collection_strategy and task.collection_strategy.startswith("LIMIT "):
            try:
                limit_n = int(task.collection_strategy.split(" ")[1])
            except ValueError:
                pass
        
        total_weight = 0
        use_counts = False
        if limit_n:
            if counts and sum(counts.values()) > 0:
                total_weight = sum(counts.values())
                use_counts = True
            else:
                total_weight = len(values)

        for i, val in enumerate(values):
            # Check for Fat Partition
            est_count = counts.get(val, 0)
            
            # [NEW] Calculate Child Quota
            child_strategy = task.collection_strategy
            child_limit = None
            
            if limit_n is not None and total_weight > 0:
                w = est_count if use_counts else 1
                ratio = w / total_weight
                child_limit = int(limit_n * ratio)
                if child_limit < 1: child_limit = 1
                child_strategy = f"LIMIT {child_limit}"
            
            new_constraints = task.constraints.copy()
            new_constraints[pkey.name] = val
            
            # Optimization: If limit is small, don't recurse even if partition is fat
            effective_count = min(est_count, child_limit) if child_limit else est_count

            if effective_count > row_threshold and depth < max_depth:
                logger.info(f"Fat Partition Detected: {val} (Est: {est_count}, Limit: {child_limit}). Recursing...")
                
                if subquery_style == "natural":
                    sub_query = await self._construct_natural_language_subquery(query, pkey.name, val)
                else:
                    sub_query = f"{query}, specifically where {pkey.name} is '{val}'"
                
                sub_task = DataTask(
                    query=sub_query,
                    collection_strategy=child_strategy,
                    constraints=new_constraints,

                    parent=task,
                    execution_mode=getattr(task, 'execution_mode', 'batch')
                )
                
                sub_tasks = await self._create_plan_recursive(
                    sub_task, 
                    schema, 
                    depth=depth+1, 
                    max_depth=max_depth,
                    partitioning_method=partitioning_method,
                    row_threshold=row_threshold,
                    max_chunks=max_chunks,
                    subquery_style=subquery_style,
                    used_keys=used_keys | {pkey.name}
                )
                map_tasks.extend(sub_tasks)
            else:
                map_tasks.append(DataTask(
                    query=query,
                    constraints=new_constraints,
                    collection_strategy=child_strategy,
                    schema=schema,
                    parent=task,
                    partition_key=pkey.name,
                    partition_value=val,
                    execution_mode=getattr(task, 'execution_mode', 'batch'),
                    estimated_rows=task.estimated_rows if depth == 0 else None
                ))
                self.current_chunk_count += 1
        
        return map_tasks
        
    async def _fetch_partition_values(self, query: str, key: Column) -> List[str]:
        """Ask LLM to list all values for the partition key."""
        prompt = PARTITION_VALUES_PROMPT.format(
            query=query, 
            key_name=key.name, 
            key_description=key.description
        )
        
        response = await self.llm.agenerate(prompt, temperature=0.0)
        
        # Parse lines
        values = []
        for line in response.content.split("\n"):
            clean = line.strip("-* ").strip()
            if clean and len(clean) < 100: # Sanity check
                values.append(clean)
                
        # Limit number of partitions to prevent explosion
        if len(values) > self.max_partitions:
            logger.warning(f"Capping partition values from {len(values)} to {self.max_partitions}")
            values = values[:self.max_partitions]
            
        return values

    async def _construct_natural_language_subquery(self, base_query: str, key: str, value: str) -> str:
        """
        Constructs a natural language sub-query using an LLM rewrite to avoid robotic phrasing.
        e.g. "List all US States" + Key="Region", Value="West" -> "List all US States in the West region"
        """
        prompt = SUBQUERY_REWRITE_PROMPT.format(base_query=base_query, key=key, value=value)
        response = await self.llm.agenerate(prompt, temperature=0.0)
        return response.content.strip().strip('"')


    async def _create_plan_recursive(
        self, 
        task: DataTask, 
        schema: SchemaDefinition, 
        depth: int = 0,
        max_depth: int = 3, # V3 Default
        partitioning_method: str = "smart",
        row_threshold: int = 100,
        max_chunks: int = 100,
        subquery_style: str = "natural",
        used_keys: Optional[set] = None,
        suggested_key: Optional[str] = None # [NEW] V3
    ) -> List[DataTask]:
        
        used_keys = used_keys or set()
        
        query = task.query
        logger.info(f"Recursive Planning (Depth {depth}): {query} (Method: {partitioning_method})")
        map_tasks: List[DataTask] = []
        
        # Check Global Chunk Limit
        if hasattr(self, 'current_chunk_count') and self.current_chunk_count >= getattr(self, 'max_chunks_limit', 100):
            logger.warning(f"Global max_chunks limit ({self.max_chunks_limit}) reached. Creating flat task.")
            return [DataTask(
                query=query,
                constraints=task.constraints,
                schema=schema,
                parent=task,
                partition_key="Filter",
                partition_value="Remaining"
            )]
        
        # If too deep, fallback to flat
        if depth > max_depth:
            logger.warning(f"Max depth {max_depth} reached. Creating flat task for: {query}")
            return [DataTask(
                query=query,
                constraints=task.constraints,
                schema=schema,
                parent=task,
                partition_key="Filter",
                partition_value="Remaining"
            )]
            
        # 2. Identify Assignments (Partition Strategy)
        partition_keys = []
        
        if suggested_key and depth == 0:
            # V3 Joint Optimization: Use pre-selected strategy
            candidates = [c for c in schema.columns if c.name == suggested_key]
            if candidates:
                partition_keys = candidates
                logger.info(f"Using Joint Optimization Suggested Key: {suggested_key}")
            else:
                logger.warning(f"Suggested key {suggested_key} not found in schema. Fallback to search.")
        
        if not partition_keys:
            # Standard Search
            partition_keys = [k for k in schema.get_partition_keys() if k.name not in used_keys]
        
        if not partition_keys:
            logger.info("No partition keys found or all used. Fallback to flat.")
            return [DataTask(
                query=query,
                constraints=task.constraints,
                schema=schema,
                parent=task,
                partition_key="All",
                partition_value="All"
            )]

        # Intelligent Selection (Phase 2.3)
        best_key = None
        best_stats = None
        
        # If we have a single forced key (suggested), skip evaluation
        if suggested_key and len(partition_keys) == 1:
            best_key = partition_keys[0]
            # Optionally fetch stats? 
            # For now assume it's good.
            selected_stats = None
        elif partitioning_method == "greedy":
            # LEGACY / GREEDY MODE
            best_key = partition_keys[0]
            selected_stats = None
        else:
            # SEMANTIC / SMART MODE (Local Evaluation)
            best_score = float('inf')
            
            for pkey in partition_keys:
                stats = await self.dist_estimator.estimate(query, pkey.name)
                # ... Use simplified scoring logic (similar to Evaluator but local) ...
                score = (stats.ambiguity_score * 1000)
                if stats.num_partitions > 50: score += 10000
                if stats.num_partitions < 2: score += 5000
                
                if best_stats is None or score < best_score:
                    best_score = score
                    best_key = pkey
                    best_stats = stats
            
            if best_stats and best_stats.ambiguity_score > 0.8:
                return [DataTask(
                    query=query,
                    constraints=task.constraints,
                    schema=schema,
                    parent=task,
                    partition_key="All",
                    partition_value="All"
                )]
            selected_stats = best_stats

        pkey = best_key
        
        # 3. Fetch Values & Recurse
        if selected_stats and selected_stats.partition_counts:
            values = list(selected_stats.partition_counts.keys())
            counts = selected_stats.partition_counts
        else:
            values = await self._fetch_partition_values(query, pkey, task.constraints)
            counts = {}

        logger.info(f"Selected Key: {pkey.name}. Values: {len(values)}")

        # [FIX] Zero-Value Fallback: If no values generated, revert to flat task
        if not values:
            logger.warning(f"Partition key {pkey.name} generated NO values (Local Path). Falling back to FLAT execution.")
            return [DataTask(
                query=query,
                constraints=task.constraints,
                collection_strategy=task.collection_strategy,
                schema=schema,
                parent=task,
                partition_key="All",
                partition_value="Fallback",
                execution_mode=getattr(task, 'execution_mode', 'batch'),
                estimated_rows=task.estimated_rows
            )]

        for i, val in enumerate(values):
            # Check for Fat Partition
            est_count = counts.get(val, 0)
            
            # Create Task Context
            new_constraints = task.constraints.copy()
            new_constraints[pkey.name] = val
            
            if est_count > row_threshold and depth < max_depth:
                logger.info(f"Fat Partition Detected: {val} (Est: {est_count}). Recursing...")
                
                if subquery_style == "natural":
                    sub_query = await self._construct_natural_language_subquery(query, pkey.name, val)
                else:
                    sub_query = f"{query}, specifically where {pkey.name} is '{val}'"
                
                sub_task = DataTask(
                    query=sub_query,
                    collection_strategy=task.collection_strategy,
                    constraints=new_constraints,
                    parent=task
                )
                
                sub_tasks = await self._create_plan_recursive(
                    sub_task, 
                    schema, 
                    depth=depth+1, 
                    max_depth=max_depth,
                    partitioning_method=partitioning_method,
                    row_threshold=row_threshold,
                    max_chunks=max_chunks,
                    subquery_style=subquery_style,
                    used_keys=used_keys | {pkey.name}
                )
                map_tasks.extend(sub_tasks)
            else:
                map_tasks.append(DataTask(
                    query=query,
                    constraints=new_constraints,
                    schema=schema,
                    parent=task,
                    partition_key=pkey.name,
                    partition_value=val
                ))
                self.current_chunk_count += 1
        
        return map_tasks
        
    async def _fetch_partition_values(self, query: str, key: Column, constraints: Dict[str, Any] = {}) -> List[str]:
        """
        Ask LLM to list all values for the partition key.
        e.g. "List all States"
        """
        prompt = PARTITION_VALUES_PROMPT.format(
            query=query,
            key_name=key.name,
            key_description=key.description,
            constraints=json.dumps(constraints, indent=2)
        )
        
        response = await self.llm.agenerate(prompt, temperature=0.0)
        
        # Parse lines
        values = []
        for line in response.content.split("\n"):
            clean = line.strip("-* ").strip()
            if clean and len(clean) < 100: # Sanity check
                values.append(clean)
                
        # Limit number of partitions to prevent explosion
        if len(values) > self.max_partitions:
            logger.warning(f"Capping partition values from {len(values)} to {self.max_partitions}")
            values = values[:self.max_partitions]
            
        return values

    async def _construct_natural_language_subquery(self, base_query: str, key: str, value: str) -> str:
        """
        Constructs a natural language sub-query using an LLM rewrite to avoid robotic phrasing.
        e.g. "List all US States" + Key="Region", Value="West" -> "List all US States in the West region"
        """
        prompt = f"""
        Base Query: "{base_query}"
        Filter: {key} = {value}
        
        Rewrite the query to naturally include the filter.
        Examples:
        - Query: "List all Pokemon", Filter: Type = Fire -> "List all Fire-type Pokemon"
        - Query: "List US Cities", Filter: State = CA -> "List all US Cities in California"
        - Query: "List Nobel Winners", Filter: Field = Physics -> "List all Nobel Prize winners in Physics"
        
        Return ONLY the rewritten query text.
        """
        response = await self.llm.agenerate(prompt, temperature=0.0)
        return response.content.strip().strip('"')

