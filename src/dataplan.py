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
    SUBQUERY_REWRITE_PROMPT,
    PARTITION_SELECTION_PROMPT
)
from utils.llm_utils import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class DistributionStats:
    key_name: str
    num_partitions: int
    max_partition_size: int
    ambiguity_score: float # [0, 1], 1 is highest ambiguity
    reasoning: str
    partition_counts: Dict[str, int] = field(default_factory=dict)

@dataclass
class StrategyCandidate:
    """
    Candidate for (Schema, PartitionKey) pair.
    """
    schema: SchemaDefinition
    partition_key: str
    score: Optional[float] = None # optional
    stats: Optional[DistributionStats] = None
    reasoning: str = ""

class StrategyEvaluator:
    """
    Jointly evaluates (Schema, PartitionKey) candidates.
    """
    def __init__(self, llm_client: LLMClient, enable_scoring: bool = False):
        self.estimator = DistributionEstimator(llm_client)
        self.enable_scoring = enable_scoring

    async def evaluate_concurrent(self, query: str, candidates: List[StrategyCandidate], constraints: dict = {}, total_rows: int = 0) -> List[StrategyCandidate]:
        """
        Runs distribution estimation in parallel for all candidates.
        """
        async def evaluate_one(cand: StrategyCandidate) -> StrategyCandidate:
            stats = await self.estimator.estimate(query, cand.partition_key, constraints, total_rows=total_rows)
            cand.stats = stats
            
            if not self.enable_scoring:
                return cand

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
            
            # Heuristic Penalties
            key_lower = cand.partition_key.lower()
            if "zip" in key_lower and "code" in key_lower:
                score -= 80
                stats.reasoning += " [Penalty: Zip Code is poor for summaries]"
            
            if key_lower in ["id", "rowid", "index", "uuid"]:
                score -= 60
                stats.reasoning += " [Penalty: Avoid generic IDs]"
                
            # Semantic Type Logic
            # Find the column definition
            if cand.schema:
                col_def = next((c for c in cand.schema.columns if c.name == cand.partition_key), None)
                if col_def:
                    sem_type = col_def.semantic_type.lower()
                    
                    # Boost for good types
                    if sem_type in ["time", "categorical", "location", "entity"]:
                        score += 20
                        stats.reasoning += f" [Boost: Good Semantic Type '{sem_type}']"
                        
                    # Penalty for numerical measures (unless requested in constraints)
                    elif sem_type == "numerical":
                        # Check if this key is strictly required by constraints
                        is_constraint = cand.partition_key in constraints
                        if not is_constraint:
                            score -= 50
                            stats.reasoning += f" [Penalty: Avoid numerical measures '{sem_type}']"
                        else:
                            stats.reasoning += f" [Neutral: Numerical but requested in constraints]"
            
            cand.score = score
            cand.reasoning = stats.reasoning
            return cand

        # Run all
        tasks = [evaluate_one(c) for c in candidates]
        results = await asyncio.gather(*tasks)
        return results

    async def select_best_llm(self, query: str, candidates: List[StrategyCandidate], constraints: dict = {}) -> Optional[StrategyCandidate]:
        if not candidates:
            return None
            
        candidates_info = []
        for c in candidates:
            # Lookup semantic type and description
            sem_type = "Unknown"
            description = ""
            if c.schema:
                col_def = next((col for col in c.schema.columns if col.name == c.partition_key), None)
                if col_def:
                    sem_type = col_def.semantic_type
                    description = col_def.description

            info = (
                f"- Key: {c.partition_key}\n"
                f"  Semantic Type: {sem_type}\n"
                f"  Description: {description}\n"
                f"  Number of Partitions: {c.stats.num_partitions}\n"
                f"  Max Partition Size: {c.stats.max_partition_size}\n"
                f"  Ambiguity Score: {c.stats.ambiguity_score} (0=Clear, 1=Ambiguous)\n"
                f"  Stats Reasoning: {c.stats.reasoning}"
            )
            if c.stats.partition_counts:
                max_partitions_in_prompt = 50
                partition_items = c.stats.partition_counts
                if len(partition_items) > max_partitions_in_prompt:
                    # partition_counts is a dict; slice by converting to list of keys
                    half = max_partitions_in_prompt // 2
                    keys = list(partition_items.keys())
                    truncated = {k: partition_items[k] for k in keys[:half]}
                    truncated["..."] = "..."
                    truncated.update({k: partition_items[k] for k in keys[-half:]})
                    info += f"\n  Estimated Number of Records in Each Partition (truncated, {len(partition_items)} total): {truncated}"
                else:
                    info += f"\n  Estimated Number of Records in Each Partition: {partition_items}"
            candidates_info.append(info)
        
        candidates_str = "\n\n".join(candidates_info)
        
        prompt = PARTITION_SELECTION_PROMPT.format(
            query=query,
            constraints=json.dumps(constraints, indent=2),
            candidates_str=candidates_str
        )
        
        try:
            response = await self.estimator.llm.agenerate(prompt, response_format={"type": "json_object"})
            data = json.loads(response.content)
            option = data.get("option", "A")
            selected_key = data.get("selected_key")
            reasoning = data.get("reasoning", "")

            logger.info(f"LLM Selected Partition Option {option}: key='{selected_key}' (Reason: {reasoning})")

            # Option C: latent dimension
            if option == "C":
                any_ambiguous = any(
                    (c.stats.ambiguity_score >= 0.7 if c.stats else False)
                    for c in candidates
                )
                if not any_ambiguous:
                    logger.warning("Option C requested but no candidates have ambiguity_score >= 0.7. Proceeding with latent dimension anyway (LLM's explicit choice).")
                # Trust the LLM's Option C decision — return synthetic candidate for the latent dimension
                synthetic = StrategyCandidate(
                    schema=candidates[0].schema,
                    partition_key=selected_key,
                    score=0.5,
                    reasoning=f"Option C latent dimension [{reasoning}]"
                )
                return synthetic

            # Options A and B: find matching schema candidate
            for c in candidates:
                if c.partition_key == selected_key:
                    c.reasoning += f" [LLM Option {option}: {reasoning}]"
                    return c

            # Fallback if name mismatch
            logger.warning(f"LLM selected key '{selected_key}' not found in candidates. Falling back to score.")
            return self.select_best(candidates)

        except Exception as e:
            logger.error(f"LLM Partition Selection failed: {e}. Falling back to score.")
            return self.select_best(candidates)

    def select_best(self, candidates: List[StrategyCandidate]) -> Optional[StrategyCandidate]:
        if not candidates:
            return None
        # Sort by score descending; treat None scores as 0 to avoid comparison crash
        sorted_cands = sorted(candidates, key=lambda x: x.score or 0, reverse=True)
        return sorted_cands[0]

class DistributionEstimator:
    """
    Estimates the data distribution for a given partition key.
    Helps avoid "Fat Partitions" or "Dust" (too many small partitions).
    """
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def estimate(self, query: str, key_name: str, constraints: dict = {}, total_rows: int = 0) -> DistributionStats:
        prompt = DISTRIBUTION_ESTIMATION_PROMPT.format(
            query=query,
            key_name=key_name,
            constraints=json.dumps(constraints, indent=2),
            total_rows=total_rows if total_rows else "unknown"
        )
        try:
            response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
        except Exception as e:
            logger.error(f"Distribution estimation API call failed for {key_name}: {e}")
            return DistributionStats(
                key_name=key_name,
                num_partitions=0,
                max_partition_size=0,
                ambiguity_score=1.0,
                reasoning=f"API call failed: {str(e)}",
                partition_counts={}
            )
        
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            data = json.loads(content)
            
            partition_counts = data.get("partitions", {})
            # Auto-calculate stats from the detailed counts
            if partition_counts:
                num_partitions = len(partition_counts)
                # Ensure values are ints, handle non-numeric values gracefully
                try:
                    max_partition_size = max(int(v) for v in partition_counts.values() if v is not None) if partition_counts else 0
                except (ValueError, TypeError) as ve:
                    logger.warning(f"Non-numeric partition counts for {key_name}: {ve}. Using 0.")
                    max_partition_size = 0
            else:
                num_partitions = int(data.get("num_partitions", 0))
                max_partition_size = int(data.get("max_partition_size", 0))
            
            # Validate ambiguity_score is in valid range
            ambiguity_score = data.get("ambiguity_score", 0.5)
            if not isinstance(ambiguity_score, (int, float)) or ambiguity_score < 0.0 or ambiguity_score > 1.0:
                logger.warning(f"Invalid ambiguity_score for {key_name}: {ambiguity_score}. Clamping to [0.0, 1.0].")
                ambiguity_score = max(0.0, min(1.0, float(ambiguity_score)))
            
            return DistributionStats(
                key_name=key_name,
                num_partitions=num_partitions,
                max_partition_size=max_partition_size,
                ambiguity_score=float(ambiguity_score),
                reasoning=data.get("reasoning", ""),
                partition_counts=partition_counts
            )
        except json.JSONDecodeError as je:
            logger.error(f"Distribution estimation JSON parsing failed for {key_name}: {je}. Response content: {response.content[:200]}")
            return DistributionStats(
                key_name=key_name,
                num_partitions=0,
                max_partition_size=0,
                ambiguity_score=1.0,
                reasoning=f"JSON parsing failed: {str(je)}",
                partition_counts={}
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Distribution estimation data processing failed for {key_name}: {e}. Data: {data if 'data' in locals() else 'N/A'}")
            return DistributionStats(
                key_name=key_name,
                num_partitions=0,
                max_partition_size=0,
                ambiguity_score=1.0,
                reasoning=f"Data processing error: {str(e)}",
                partition_counts={}
            )
        except Exception as e:
            logger.error(f"Distribution estimation unexpected error for {key_name}: {e}", exc_info=True)
            return DistributionStats(
                key_name=key_name,
                num_partitions=0,
                max_partition_size=0,
                ambiguity_score=1.0,
                reasoning=f"Unexpected error: {str(e)}",
                partition_counts={}
            )


@dataclass
class DataPlanRequest:
    task: DataTask # The Root Task
    schema: SchemaDefinition
    map_tasks: List[DataTask] # List of DataTasks (Leaf Nodes)
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
        temperature: float = 0.7,
        row_threshold: int = 100,
        max_depth: int = 3,
        partitioning_method: str = "llm_based",  # "llm_based" (evaluates candidates) or "first" (picks first column)
        decomposition_strategy: str = "llm_based",
        partition_selection_mode: str = "llm",  # scoring or llm
        enable_heuristic_scoring: bool = False
    ):
        self.llm = llm_client
        self.max_partitions = max_partitions
        self.num_candidates = num_candidates
        self.temperature = temperature
        
        # Defaults
        self.default_row_threshold = row_threshold
        self.default_max_depth = max_depth
        self.default_partitioning_method = partitioning_method
        self.default_decomposition_strategy = decomposition_strategy
        self.default_partition_selection_mode = partition_selection_mode
        self.enable_heuristic_scoring = enable_heuristic_scoring
        
        self.analyzer = QueryAnalyzer(llm_client)
        self.schema_gen = SchemaGenerator(llm_client)
        self.evaluator = StrategyEvaluator(llm_client, enable_scoring=enable_heuristic_scoring)
        self.dist_estimator = DistributionEstimator(llm_client)
        
    async def create_plan(
        self, 
        query: str, 
        row_threshold: Optional[int] = None,
        decomposition_strategy: Optional[str] = None, 
        partitioning_method: Optional[str] = None, 
        subquery_style: str = "natural",
        max_chunks: int = 100,
        context: dict = None,
        schema: Optional[SchemaDefinition] = None,
        execution_mode: str = "batch",
        max_depth: Optional[int] = None,
        source: str = "Wikipedia",
        partition_selection_mode: Optional[str] = None,
        **kwargs
    ) -> DataPlanRequest:
        
        # Apply Defaults
        row_threshold = row_threshold if row_threshold is not None else self.default_row_threshold
        decomposition_strategy = decomposition_strategy or self.default_decomposition_strategy
        partitioning_method = partitioning_method or self.default_partitioning_method
        partition_selection_mode = partition_selection_mode or self.default_partition_selection_mode
        
        context = context or {}
        self.max_chunks_limit = max_chunks # Store for recursive tracking
        self.current_chunk_count = 0
        
        # 0. Analyze Query
        # Get rewritten query and constraints, but defer decomposition decision until after schema generation
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
            estimated_rows=analysis.estimated_rows,
            source=source
        )
        root_task.schema = None # Will be set below
        
        rewritten_query = root_task.query
        final_schema = None
        best_strategy = None
        requires_decomposition = False
        
        # 1. EARLY SCHEMA GENERATION (Before decomposition decision)
        # Generate schema options that we'll evaluate for decomposition
        schema_options = []
        
        if schema:
            # User provided schema - use it
            logger.info(f"Using provided strict Schema: {schema.table_name}")
            final_schema = await self.schema_gen.infer_schema(rewritten_query, input_schema=schema)
            schema_options = [(final_schema, "provided")]
            
        elif partitioning_method == "llm_based":
            # Generate multiple schema candidates for joint optimization
            logger.info("Generating schema candidates for joint optimization...")
            try:
                pool = await self.schema_gen.generate_pool(rewritten_query)
                raw_candidates = await self.schema_gen.generate_candidates(rewritten_query, k=3, column_pool=pool)
                
                for i, raw_cand in enumerate(raw_candidates):
                    schema_def = await self.schema_gen.infer_enrichment(rewritten_query, raw_cand)
                    schema_options.append((schema_def, f"candidate_{i}"))
                    logger.info(f"Generated schema candidate {i}: {schema_def.table_name} with {len(schema_def.columns)} columns")
            except Exception as e:
                logger.warning(f"Schema candidate generation failed: {e}. Falling back to single schema.")
                strategy = "llm_rank"
                fallback_schema = await self.schema_gen.infer_schema(rewritten_query, strategy=strategy, context=context)
                schema_options = [(fallback_schema, "fallback")]
        else:
            # Generate single schema with first mode
            strategy = "first" if partitioning_method == "first" else "llm_rank"
            single_schema = await self.schema_gen.infer_schema(rewritten_query, strategy=strategy, context=context)
            schema_options = [(single_schema, "single")]
        
        # 2. FOR EACH SCHEMA, MAKE DECOMPOSITION DECISION (Schema-aware)
        # Evaluate all schema options and select the best one
        best_overall_score = float('inf')
        best_final_schema = None
        best_selected_strategy = None
        best_requires_decomposition = False
        
        for schema_candidate, source_label in schema_options:
            # Make decomposition decision for THIS schema
            schema_requires_decomposition = False
            schema_reasoning = "No decomposition"
            
            if decomposition_strategy == "none":
                schema_requires_decomposition = False
                schema_reasoning = "Forced flat execution by 'none' strategy"
            else:
                # For llm_based or rule_based, use generic estimate as baseline
                # (In future, could enhance to estimate rows per this specific schema)
                if analysis.estimated_rows > row_threshold:
                    schema_requires_decomposition = True
                    schema_reasoning = f"Rows ({analysis.estimated_rows}) > threshold ({row_threshold})"
                elif decomposition_strategy == "llm_based" and analysis.estimated_rows is None:
                    # Only consult LLM when row count is unknown; if we have a reliable
                    # estimate below the threshold, trust it and go flat.
                    schema_requires_decomposition, schema_reasoning = await self.analyzer.should_decompose(rewritten_query, analysis.estimated_rows)
            
            logger.info(f"Schema {source_label}: requires_decomposition={schema_requires_decomposition} ({schema_reasoning})")
            
            # Evaluate partition keys if decomposition needed for this schema
            selected_strategy = None
            if schema_requires_decomposition:
                if partitioning_method == "first":
                    # First mode: just pick first column
                    if schema_candidate.columns:
                        selected_strategy = StrategyCandidate(
                            schema=schema_candidate,
                            partition_key=schema_candidate.columns[0].name,
                            score=0.0,
                            reasoning="First mode: using first available column"
                        )
                        logger.info(f"[{source_label}] First mode: Selected '{selected_strategy.partition_key}' as partition key")
                else:
                    # Evaluated mode: evaluate all columns for this schema
                    logger.info(f"[{source_label}] Evaluating partition keys...")
                    strategy_candidates = []
                    
                    for col in schema_candidate.columns:
                        strategy_candidates.append(StrategyCandidate(
                            schema=schema_candidate,
                            partition_key=col.name
                        ))
                    
                    if strategy_candidates:
                        # Evaluate concurrently
                        evaluated = await self.evaluator.evaluate_concurrent(rewritten_query, strategy_candidates, constraints=root_task.constraints, total_rows=analysis.estimated_rows or 0)
                        
                        # Select best
                        if partition_selection_mode == "llm":
                            selected_strategy = await self.evaluator.select_best_llm(rewritten_query, evaluated, constraints=root_task.constraints)
                        else:
                            selected_strategy = self.evaluator.select_best(evaluated)
                        
                        if selected_strategy:
                            logger.info(f"[{source_label}] Selected partition key: {selected_strategy.partition_key} (score: {selected_strategy.score})")
            else:
                # No decomposition for this schema - create dummy strategy
                selected_strategy = StrategyCandidate(
                    schema=schema_candidate,
                    partition_key="All",
                    score=0.0,
                    reasoning="No decomposition needed"
                )
            
            # Score this (schema, decomposition, strategy) combination
            # Prefer: no decomposition (simpler) > fewer partitions > better partition score
            combo_score = 0.0
            if schema_requires_decomposition:
                combo_score += 100  # Penalty for decomposition needed
                if selected_strategy and selected_strategy.stats:
                    combo_score += selected_strategy.stats.num_partitions * 5  # Penalty for many partitions
            
            logger.info(f"[{source_label}] Combination score: {combo_score}")
            
            # Update best if this is better
            if combo_score < best_overall_score:
                best_overall_score = combo_score
                best_final_schema = schema_candidate
                best_requires_decomposition = schema_requires_decomposition
                best_selected_strategy = selected_strategy
                logger.info(f"[{source_label}] New best schema selected (score: {combo_score})")
        
        # 3. USE THE BEST SCHEMA + DECOMPOSITION DECISION
        final_schema = best_final_schema or schema_options[0][0]  # Fallback to first if none selected
        requires_decomposition = best_requires_decomposition
        best_strategy = best_selected_strategy
        
        logger.info(f"FINAL DECISION: Schema={final_schema.table_name}, requires_decomposition={requires_decomposition}, partition_key={best_strategy.partition_key if best_strategy else 'None'}")
        
        # Attach schema to root task
        root_task.schema = final_schema
        
        map_tasks: List[DataTask] = []
        
        # Decide: Flat vs Partitioned
        if not requires_decomposition:
            logger.info("Decomposition not required. Creating single flat task.")
            flat_task = DataTask(
                query=rewritten_query,
                constraints=root_task.constraints,
                collection_strategy=root_task.collection_strategy,
                schema=final_schema,
                parent=root_task,
                partition_key="All",
                partition_value="All",
                execution_mode=root_task.execution_mode,
                estimated_rows=root_task.estimated_rows,
                source=source
            )
            map_tasks.append(flat_task)
        else:
            # Recursive Plan Generation
            pass_key = best_strategy.partition_key if best_strategy else None
            pass_stats = best_strategy.stats if best_strategy else None
            
            map_tasks = await self._create_plan_recursive(
                root_task, 
                final_schema, 
                depth=0, 
                max_depth=kwargs.get('max_depth', 2),
                partitioning_method=partitioning_method,
                row_threshold=row_threshold,
                max_chunks=max_chunks,
                suggested_key=pass_key,
                suggested_stats=pass_stats,
                source=source
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
        max_depth: int = 2,
        partitioning_method: str = "llm_based",  # "llm_based" (evaluates candidates) or "first" (picks first column)
        row_threshold: int = 100,
        max_chunks: int = 100,
        subquery_style: str = "natural",
        used_keys: Optional[set] = None,
        suggested_key: Optional[str] = None,
        suggested_stats: Optional[DistributionStats] = None,
        source: str = "Wikipedia"
    ) -> List[DataTask]:
        
        used_keys = used_keys or set()
        # Treat constrained keys as 'used' to prevent redundant partitioning
        if task.constraints:
            used_keys = used_keys | set(task.constraints.keys())
        
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
                partition_value="Remaining",
                source=source
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
                estimated_rows=task.estimated_rows if depth == 0 else None,
                source=source
            )]
            
        # 2. Identify Assignments (Partition Strategy)
        partition_keys = []
        selected_stats = None
        
        if suggested_key and depth == 0:
            # Joint Optimization: Use pre-selected strategy
            candidates = [c for c in schema.columns if c.name == suggested_key]
            if candidates:
                partition_keys = candidates
                logger.info(f"Using Joint Optimization Suggested Key: {suggested_key}")
                if suggested_stats:
                    selected_stats = suggested_stats
                    logger.info("Using Cached Distribution Stats from Joint Optimization.")
            else:
                # Option C latent dimension: not in schema — create a synthetic Column for partitioning
                synth_col = Column(
                    name=suggested_key,
                    description=f"Latent dimension: {suggested_key}",
                    semantic_type="categorical"
                )
                partition_keys = [synth_col]
                logger.info(f"Using latent dimension '{suggested_key}' for partitioning (Option C).")
        
        if not partition_keys:
            # Standard Search
            logger.info(f"Depth {depth}: Used Keys: {used_keys}")
            partition_keys = [k for k in schema.columns if k.name not in used_keys]
            logger.info(f"Depth {depth}: Available Candidates: {[k.name for k in partition_keys]}")
        
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
            # Use suggested stats if available
            if not selected_stats:
                selected_stats = None
        elif partitioning_method == "first":
            best_key = partition_keys[0]
            selected_stats = None
        else:
            # SEMANTIC / EVALUATED MODE (Local Evaluation)
            best_score = float('inf')
            
            for pkey in partition_keys:
                stats = await self.dist_estimator.estimate(query, pkey.name, constraints=task.constraints, total_rows=task.estimated_rows or 0)
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
                    estimated_rows=task.estimated_rows if depth == 0 else None,
                    source=source
                )]
            selected_stats = best_stats

        pkey = best_key
        
        # 3. Fetch Values & Recurse
        if selected_stats and selected_stats.partition_counts:
            values = list(selected_stats.partition_counts.keys())
            counts = selected_stats.partition_counts
        else:
            pk_hint = schema.primary_keys[0] if schema and schema.primary_keys else ""
            values = await self._fetch_partition_values(
                query, pkey, task.constraints,
                primary_key=pk_hint,
                estimated_rows=task.estimated_rows or 0
            )
            counts = {}

        logger.info(f"Selected Key: {pkey.name}. Values: {len(values)}. Counts Keys: {list(counts.keys())}, Counts Values: {list(counts.values())}")

        # Degenerate-Partition Fallback: 1 value = no benefit from partitioning (adds confusing constraint)
        if len(values) == 1:
            logger.warning(f"Partition key {pkey.name} yielded only 1 value '{values[0]}'. Falling back to FLAT execution.")
            return [DataTask(
                query=query,
                constraints=task.constraints,
                collection_strategy=task.collection_strategy,
                schema=schema,
                parent=task,
                partition_key="All",
                partition_value="All",
                execution_mode=getattr(task, 'execution_mode', 'batch'),
                estimated_rows=task.estimated_rows,
                source=source
            )]

        # Zero-Value Fallback: If no values generated (e.g. too many, or unknown), revert to flat task
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
                estimated_rows=task.estimated_rows,
                source=source
            )]

        # Smart Quota Allocation Setup
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
            logger.info(f"Checking Partition: '{val}' | Est: {est_count} | Threshold: {row_threshold} | Depth: {depth}/{max_depth}")
            
            # Create Task Context
            new_constraints = task.constraints.copy()
            new_constraints[pkey.name] = val
            
            # Calculate Child Quota
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
                    sub_query = await self._construct_natural_language_subquery(
                        base_query=query, 
                        key=pkey.name, 
                        value=val,
                        constraints=task.constraints,
                        strategy=child_strategy
                    )
                else:
                    sub_query = f"{query}, specifically where {pkey.name} is '{val}'"
                
                sub_task = DataTask(
                    query=sub_query,
                    collection_strategy=child_strategy,
                    constraints=new_constraints,
                    parent=task,
                    execution_mode=getattr(task, 'execution_mode', 'batch'),
                    estimated_rows=effective_count, # Pass down partition estimate
                    source=source
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
                    used_keys=used_keys | {pkey.name},
                    source=source
                )
                map_tasks.extend(sub_tasks)
            else:
                # Generate Natural Language Subquery for Leaf Nodes too
                if subquery_style == "natural":
                    sub_query = await self._construct_natural_language_subquery(
                        base_query=query, 
                        key=pkey.name, 
                        value=val,
                        constraints=task.constraints,
                        strategy=child_strategy
                    )
                else:
                    sub_query = f"{query}, specifically where {pkey.name} is '{val}'"

                map_tasks.append(DataTask(
                    query=sub_query,
                    constraints=new_constraints,
                    collection_strategy=child_strategy,
                    schema=schema,
                    parent=task,
                    partition_key=pkey.name,
                    partition_value=val,
                    execution_mode=getattr(task, 'execution_mode', 'batch'),
                    estimated_rows=effective_count, # Use partition estimate
                    source=source
                ))
                self.current_chunk_count += 1
        
        return map_tasks
        
    async def _fetch_partition_values(self, query: str, key: Column, constraints: dict = {}, primary_key: str = "", estimated_rows: int = 0) -> List[str]:
        """Ask LLM to list all values for the partition key."""
        prompt = PARTITION_VALUES_PROMPT.format(
            query=query,
            key_name=key.name,
            key_description=key.description,
            primary_key=primary_key,
            max_partitions=self.max_partitions,
            constraints=json.dumps(constraints, indent=2)
        )

        try:
            # Use JSON mode and robust parsing
            response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
            data = json.loads(response.content)

            # Expecting {"values": [...]} or list
            if isinstance(data, dict):
                raw_values = data.get("values", [])
            elif isinstance(data, list):
                raw_values = data
            else:
                logger.warning(f"Unexpected partition values format: {type(data)}")
                raw_values = []

            # Filter and Clean
            values = []
            for v in raw_values:
                if isinstance(v, (str, int, float)):
                    s = str(v).strip()
                    if s and len(s) < 100:
                        values.append(s)

            # Ratio check: if values >> estimated rows, likely an explosion — fall back to flat
            if estimated_rows and len(values) > 2 * estimated_rows:
                logger.warning(f"Partition explosion: {len(values)} values for ~{estimated_rows} estimated rows. Falling back to flat.")
                return []
            elif len(values) > self.max_partitions:
                logger.warning(f"Capping partition values from {len(values)} to {self.max_partitions}")
                values = values[:self.max_partitions]

            return values

        except Exception as e:
            logger.warning(f"Failed to fetch partition values for {key.name}: {e}")
            return []

    async def _construct_natural_language_subquery(self, base_query: str, key: str, value: str, constraints: dict = {}, strategy: str = "ALL") -> str:
        """
        Constructs a natural language sub-query using an LLM rewrite to avoid robotic phrasing.
        e.g. "List all US States" + Key="Region", Value="West" -> "List all US States in the West region"
        """
        prompt = SUBQUERY_REWRITE_PROMPT.format(
            base_query=base_query, 
            key=key, 
            value=value,
            constraints=constraints,
            strategy=strategy
        )
        response = await self.llm.agenerate(prompt, temperature=0.0)
        rewritten = response.content.strip().strip('"').strip("'")
        
        # [Validation] If rewrite is identical to base (failed to rewrite), force structured format
        if rewritten.lower() == base_query.lower() or len(rewritten) < len(base_query) * 0.5:
            logger.warning(f"Subquery rewrite validation failed/same as parent. Falling back to template. ('{rewritten}')")
            return f"{base_query}, specifically where {key} is '{value}'"
            
        logger.info(f"Subquery Rewrite: '{base_query}' + ({key}={value}) -> '{rewritten}'")
        return rewritten

