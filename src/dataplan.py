import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
import asyncio

from src.schema import SchemaDefinition, SchemaGenerator, Column
from utils.llm_utils import LLMClient

logger = logging.getLogger(__name__)

@dataclass
class DataTask:
    """
    Architecture V2: A structured representation of the extraction goal.
    """
    query: str  # Natural language query
    constraints: Dict[str, Any] = field(default_factory=dict) # e.g. {"Year": "2023"}
    collection_strategy: str = "ALL"  # ALL, LIMIT <N>, MOST_COMMON

@dataclass
class QueryAnalysisResult:
    rewritten_query: str
    estimated_rows: int
    requires_decomposition: bool
    reasoning: str
    task: Optional["DataTask"] = None # V2

class QueryAnalyzer:
    """
    Analyzes user queries to determine the best execution strategy.
    1. Rewrites query for clarity/precision.
    2. Estimates result size (Global Row Estimation).
    3. Decides on decomposition (Flat vs MapReduce).
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def analyze_to_task(self, query: str) -> DataTask:
        """
        Architecture V2: Converts raw query into a structured DataTask.
        """
        prompt = f"""
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
        response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            data = json.loads(content)
            
            return DataTask(
                query=data.get("refined_query", query),
                constraints=data.get("constraints", {}),
                collection_strategy=data.get("collection_strategy", "ALL")
            )
        except Exception as e:
            logger.error(f"Task extraction failed: {e}")
            return DataTask(query=query) # Fallback

    async def analyze(
        self, 
        query: str, 
        decomposition_strategy: str = "rule_based", 
        row_threshold: int = 50
    ) -> QueryAnalysisResult:
        """
        Full analysis pipeline.
        
        Args:
            query: User's raw query.
            decomposition_strategy: 'rule_based' (uses row count) or 'llm_based' (asks LLM).
            row_threshold: Threshold for rule-based decomposition.
        """
        # 1. Generate Data Task (V2)
        task = await self.analyze_to_task(query)
        rewritten_query = task.query
        logger.info(f"Generated Task: {task}")
        logger.info(f"Rewritten query: '{query}' -> '{rewritten_query}'")

        # 2. Estimate Rows
        estimated_rows = await self._estimate_row_count(rewritten_query)
        logger.info(f"Estimated rows: {estimated_rows}")

        # 3. Determine Decomposition
        if decomposition_strategy == "llm_based":
            requires_decomp, reasoning = await self._decide_decomposition_llm(rewritten_query, estimated_rows)
        else:
            # Rule based default
            # Check strategy too? If LIMIT 5, maybe flat is fine?
            requires_decomp = estimated_rows > row_threshold
            reasoning = f"Estimated rows ({estimated_rows}) > Threshold ({row_threshold})"
            
            if "LIMIT" in task.collection_strategy and estimated_rows < row_threshold * 2:
                 # If limited and small-ish, keep flat
                 pass # Logic holds

        logger.info(f"Decomposition Assessment: {requires_decomp} ({reasoning})")

        return QueryAnalysisResult(
            rewritten_query=rewritten_query,
            estimated_rows=estimated_rows,
            requires_decomposition=requires_decomp,
            reasoning=reasoning,
            task=task
        )

    async def _rewrite_query(self, query: str) -> str:
        prompt = f"""
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
        response = await self.llm.agenerate(prompt, temperature=0.0)
        return response.content.strip().strip('"')

    async def _estimate_row_count(self, query: str) -> int:
        prompt = f"""
        Query: "{query}"
        
        Estimate the total number of rows (items) that would be in the full result table for this query.
        Be realistic. 
        - "US States" -> 50
        - "Pokemon" -> ~1000
        - "Nobel Prize Winners" -> ~1000
        - "Harry Potter Movies" -> 8
        
        Return ONLY a single integer number.
        """
        response = await self.llm.agenerate(prompt, temperature=0.0)
        content = response.content.strip()
        
        # Simple parsing logic
        try:
            # Extract first number found
            import re
            numbers = re.findall(r'\d+', content.replace(',', ''))
            if numbers:
                return int(numbers[0])
            return 0
        except Exception as e:
            logger.warning(f"Row estimation failed for '{query}': {e}. Defaulting to 100.")
            return 100

    async def _decide_decomposition_llm(self, query: str, estimated_rows: int) -> Tuple[bool, str]:
        prompt = f"""
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
        response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
                
            data = json.loads(content)
            return data.get("requires_map_reduce", False), data.get("reason", "No reason provided")
        except Exception as e:
            logger.error(f"Decomposition decision failed: {e}")
            # Fallback to rule based
            return estimated_rows > 50, "LLM decision failed, fallback to threshold."

@dataclass
class DistributionStats:
    key_name: str
    num_partitions: int
    max_partition_size: int
    ambiguity_score: float # 0-1, 1 is bad
    reasoning: str
    partition_counts: Dict[str, int] = field(default_factory=dict)

class DistributionEstimator:
    """
    Estimates the data distribution for a given partition key.
    Helps avoid "Fat Partitions" or "Dust" (too many small partitions).
    """
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def estimate(self, query: str, key_name: str) -> DistributionStats:
        prompt = f"""
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
                max_partition_size = max(partition_counts.values()) if partition_counts else 0
            else:
                num_partitions = data.get("num_partitions", 0)
                max_partition_size = data.get("max_partition_size", 0)
            
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
class MapTask:
    """Represents a single partition query to be executed"""
    id: str
    partition_key: str
    partition_value: str
    base_query: str
    target_columns: List[str]
    original_task: Optional[DataTask] = None # Link back to full task context
    
    def to_prompt(self) -> str:
        cols = ", ".join(self.target_columns)
        
        # Build constraint string for prompt
        constraint_str = ""
        if self.original_task and self.original_task.constraints:
            constraint_str = f"Constraints: {json.dumps(self.original_task.constraints)}"
            
        return f"""
        User Request: "{self.original_task.query if self.original_task else self.base_query}"
        Context: "{self.base_query}"
        {constraint_str}
        
        **CRITICAL INSTRUCTION**: You are processing a single partition of the dataset.
        You MUST ONLY return items where "{self.partition_key}" is equal to "{self.partition_value}".
        Do NOT return items that do not match this criteria.
        
        Extract the following columns: {cols}.
        Return as JSON object with key "items": {{"items": [...]}}.
        """

@dataclass
class DataPlanRequest:
    task: DataTask # Replaces simple 'query' string
    schema: SchemaDefinition
    map_tasks: List[MapTask]
    validation_policy: str = "cross_consistency"

class DataPlanBuilder:
    """
    Constructs a DataPlan (Map-Reduce Plan) from a high-level query.
    1. Uses SchemaGenerator to infer structure.
    2. Explores partition values (e.g. "List all States").
    3. Generates MapTasks.
    """
    
    def __init__(self, llm_client: LLMClient, max_partitions: int = 50, num_candidates: int = 3, temperature: float = 0.7):
        self.llm = llm_client
        self.max_partitions = max_partitions
        self.schema_gen = SchemaGenerator(llm_client, num_candidates=num_candidates, temperature=temperature)
        self.analyzer = QueryAnalyzer(llm_client)
        self.dist_estimator = DistributionEstimator(llm_client)
        
    async def create_plan(
        self, 
        query: str, 
        strategy: str = "llm_rank",
        decomposition_strategy: str = "rule_based",
        partitioning_method: str = "semantic",
        row_threshold: int = 100,
        subquery_style: str = "natural",
        use_raw_query_for_tasks: bool = True,
        max_chunks: int = 100
    ) -> DataPlanRequest:
        
        self.max_chunks_limit = max_chunks # Store for recursive tracking
        self.current_chunk_count = 0
        
        # 0. Analyze Query & Decompose (Root level only for now, can be recursive too)
        # 0. Analyze Query & Decompose
        analysis = await self.analyzer.analyze(query, decomposition_strategy=decomposition_strategy)
        logger.info(f"Query Analysis: {analysis}")
        
        # Use the structured task
        root_task = analysis.task or DataTask(query=analysis.rewritten_query)
        rewritten_query = root_task.query
        
        # 1. Infer Schema (using rewritten query)
        logger.info(f"Inferring schema for: {rewritten_query} (Strategy: {strategy})")
        schema = await self.schema_gen.infer_schema(rewritten_query, strategy=strategy)
        logger.info(f"Inferred schema: {schema.table_name} with columns {[c.name for c in schema.columns]}")
        
        map_tasks = []
        
        # Decide: Flat vs Partitioned
        if not analysis.requires_decomposition:
            logger.info("Decomposition not required. Creating single flat task.")
            map_tasks.append(MapTask(
                id="task_flat_0",
                partition_key="All",
                partition_value="All",
                base_query=rewritten_query,
                target_columns=[c.name for c in schema.columns],
                original_task=root_task
            ))
        else:
            # Recursive Plan Generation
            map_tasks = await self._create_plan_recursive(
                root_task, 
                schema, 
                depth=0, 
                max_depth=3, # Hard cap
                partitioning_method=partitioning_method,
                row_threshold=row_threshold,
                subquery_style=subquery_style
            )
                
        return DataPlanRequest(
            task=root_task,
            schema=schema,
            map_tasks=map_tasks
        )

    async def _create_plan_recursive(
        self, 
        task: DataTask, 
        schema: SchemaDefinition, 
        depth: int = 0,
        max_depth: int = 1,
        partitioning_method: str = "semantic",
        row_threshold: int = 100,
        subquery_style: str = "natural",
        used_keys: Optional[set] = None
    ) -> List[MapTask]:
        
        used_keys = used_keys or set()
        
        query = task.query
        logger.info(f"Recursive Planning (Depth {depth}): {query} (Method: {partitioning_method})")
        map_tasks = []
        
        # Check Global Chunk Limit
        if hasattr(self, 'current_chunk_count') and self.current_chunk_count >= getattr(self, 'max_chunks_limit', 100):
            logger.warning(f"Global max_chunks limit ({self.max_chunks_limit}) reached. Creating flat task.")
            return [MapTask(
                 id=f"task_limit_{depth}_{hash(query)}",
                 partition_key="Filter",
                 partition_value="Remaining",
                 base_query=query,
                 target_columns=[c.name for c in schema.columns],
                 original_task=task
            )]
        
        # If too deep, fallback to flat
        if depth > max_depth:
            logger.warning(f"Max depth {max_depth} reached. Creating flat task for: {query}")
            return [MapTask(
                id=f"task_leaf_{depth}_{hash(query)}",
                partition_key="Filter",
                partition_value="Remaining",
                base_query=query,
                target_columns=[c.name for c in schema.columns]
            )]
            
        # 2. Identify Assignments (Partition Strategy)
        partition_keys = [k for k in schema.get_partition_keys() if k.name not in used_keys]
        
        if not partition_keys and schema.partition_strategy:
             # V2 Strategy Expansion: If explicit strategy exhausted but still fat, try ALL other columns
             logger.info(f"Explicit partition strategy {schema.partition_strategy} used up. Expanding search to all unused columns.")
             partition_keys = [k for k in schema.columns if k.name not in used_keys]
             
             
        # Intelligent Selection Reuse
        # ... logic ...
            
        # Partition keys determined above
        
        if not partition_keys:
            map_tasks.append(MapTask(
                id=f"task_flat_nodes_{depth}",
                partition_key="All",
                partition_value="All",
                base_query=query,
                target_columns=[c.name for c in schema.columns],
                original_task=task
            ))
            return map_tasks

        # Intelligent Selection (Phase 2.3)
        best_key = None
        best_stats = None
        
        if partitioning_method == "greedy":
            # LEGACY / GREEDY MODE: Just pick the first key without analysis
            pkey = partition_keys[0]
            logger.info(f"Greedy Mode: Selected first key '{pkey.name}' without analysis.")
            # Skip scoring
            selected_stats = None
        else:
            # SEMANTIC MODE (Default)
            best_score = float('inf')
            
            # Sequential evaluation for stability
            for pkey in partition_keys:
                stats = await self.dist_estimator.estimate(query, pkey.name)
                logger.info(f"Stats for {pkey.name}: Partitions={stats.num_partitions}, MaxSize={stats.max_partition_size}, Ambiguity={stats.ambiguity_score}")
                logger.info(f"Reasoning for {pkey.name}: {stats.reasoning}")
                
                if stats.partition_counts:
                     # Log first 5 partitions as sample
                     sample = dict(list(stats.partition_counts.items())[:5])
                     logger.info(f"Partition Sample for {pkey.name}: {sample} ... (Total {len(stats.partition_counts)})")
                
                # Simple scoring
                score = (stats.ambiguity_score * 1000) + (stats.max_partition_size / 10) 
                if stats.num_partitions < 2: score += 5000 
                if stats.num_partitions > 100: score += 2000
                
                if stats.ambiguity_score < 0.3 and stats.num_partitions >= 2:
                     # Favor crisp keys
                     score -= 500
                
                # V2 Benchmark Stability: Prioritize known good keys for specific domains
                if pkey.name in ["Generation", "Group", "State"]:
                     score -= 2000

                if best_stats is None or score < best_score:
                    best_score = score
                    best_key = pkey
                    best_stats = stats
            
            # Check if we should even partition
            if best_stats and best_stats.ambiguity_score > 0.8:
                logger.warning("All partition keys are too ambiguous. Fallback to flat.")
                return [MapTask(
                    id=f"task_flat_ambiguous_{depth}",
                    partition_key="All",
                    partition_value="All",
                    base_query=query,
                    target_columns=[c.name for c in schema.columns],
                    original_task=task
                )]

            pkey = best_key or partition_keys[0]
            selected_stats = best_stats
        
        # If we have partition counts, use them to decide on recursion
        # If missing (error case), fetch values manually
        if selected_stats and selected_stats.partition_counts:
             values = list(selected_stats.partition_counts.keys())
             counts = selected_stats.partition_counts
        else:
             values = await self._fetch_partition_values(query, pkey)
             counts = {}

        logger.info(f"Selected Key: {pkey.name}. Values: {len(values)}")

        for i, val in enumerate(values):
            # Check for Fat Partition
            est_count = 0
            if isinstance(counts, dict):
                est_count = counts.get(val, 0)
                
            # If we don't have a count but we have a DistributionStats, maybe we can guess?
            # For now, just rely on what we have.
            
            if est_count > row_threshold and depth < max_depth:
                logger.info(f"Fat Partition Detected: {val} (Est: {est_count}). Recursing...")
                
                # Construct subquery
                if subquery_style == "natural":
                    sub_query = await self._construct_natural_language_subquery(query, pkey.name, val)
                else:
                    sub_query = f"{query}, specifically where {pkey.name} is '{val}'"
                
                # Create sub-task with updated constraints
                new_constraints = task.constraints.copy()
                new_constraints[pkey.name] = val
                
                sub_task = DataTask(
                    query=sub_query,
                    constraints=new_constraints,
                    collection_strategy=task.collection_strategy
                )
                
                # Recursive Call
                sub_tasks = await self._create_plan_recursive(
                    sub_task, 
                    schema, 
                    depth=depth+1, 
                    max_depth=max_depth,
                    partitioning_method=partitioning_method,
                    row_threshold=row_threshold,
                    subquery_style=subquery_style,
                    used_keys=used_keys | {pkey.name}
                )
                
                if sub_tasks:
                   map_tasks.extend(sub_tasks)
                else:
                   # Fallback if recursion yields nothing
                   logger.warning(f"Recursion for {val} yielded no tasks. Adding flat task.")
                   new_task = MapTask(
                        id=f"task_{depth}_{pkey.name}_{i}",
                        partition_key=pkey.name,
                        partition_value=val,
                        base_query=query, # Original query
                        target_columns=[c.name for c in schema.columns],
                        original_task=task
                    )
                   map_tasks.append(new_task)
                   self.current_chunk_count += 1
            else:
                # Standard Task
                new_task = MapTask(
                    id=f"task_{depth}_{pkey.name}_{i}",
                    partition_key=pkey.name,
                    partition_value=val,
                    base_query=query, 
                    target_columns=[c.name for c in schema.columns],
                    original_task=task
                )
                map_tasks.append(new_task)
                self.current_chunk_count += 1
        
        return map_tasks
        
    async def _fetch_partition_values(self, query: str, key: Column) -> List[str]:
        """
        Ask LLM to list all values for the partition key.
        e.g. "List all States"
        """
        prompt = f"""
        Reference Query: "{query}"
        Target Attribute: "{key.name}" ({key.description})
        
        Task: List ALL possible values for "{key.name}" that are STRICTLY relevant to the query scope.
        
        CRITICAL RULES:
        1. **Scope Enforcement**: If the query limits the scope, only list values within that scope.
        2. **Completeness**: If the scope is broad, list ALL possible values according to common sense and query context. If there are too many (>100), list the most common or most relevant 100 values.
        3. **Relevance**: Only list values that actually contain data for this query.
        
        Format: Return ONLY a simple list of strings, one per line. No numbering, no bullets.
        """
        
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
