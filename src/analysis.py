import json
import logging
from typing import Optional, Tuple
from dataclasses import dataclass, field
import hashlib
import time

from utils.llm_utils import LLMClient
from src.schema import SchemaDefinition
from src.prompts import (
    QUERY_ANALYSIS_PROMPT,
    QUERY_REWRITE_PROMPT,
    ROW_ESTIMATION_PROMPT,
    DECOMPOSITION_DECISION_PROMPT,
    DATA_EXTRACTION_PROMPT,
    DATA_EXTRACTION_PARTITION_CONTEXT,
    DATA_EXTRACTION_COLUMNS_SUFFIX
)

logger = logging.getLogger(__name__)

@dataclass
class DataTask:
    """
    Architecture: A Unified structured representation of an extraction goal (Node in Execution Graph).
    """
    # 1. Goal Definition
    query: str  # Natural language query
    constraints: dict = field(default_factory=dict) # e.g. {"Year": "2023"}
    collection_strategy: str = "ALL"  # ALL, LIMIT <N>, MOST_COMMON
    
    # 2. Context & Lineage
    schema: Optional[SchemaDefinition] = None  # The expected output structure
    parent: Optional["DataTask"] = None        # Pointer to creator task
    trace_id: str = field(default_factory=lambda: hashlib.md5(str(time.time()).encode()).hexdigest())
    
    # 3. Partitioning Context (If this is a leaf/partition node)
    partition_key: Optional[str] = None   # e.g. "Year"
    partition_value: Optional[str] = None # e.g. "2023"
    estimated_rows: Optional[int] = None # Estimated total rows (for completeness check)
    
    # 4. Execution Config
    execution_mode: str = "batch" # "batch" or "row_by_row"
    source: str = "Wikipedia" # Data source for the query
    
    def get_full_context(self) -> str:
        """Returns lineage string for debugging constraints"""
        if self.parent:
            return f"{self.parent.get_full_context()} -> {self.query}"
        return self.query

    def to_prompt(self) -> str:
        """Generates the execution prompt for this task."""
        # Check if we have a schema to guide extraction
        cols = "*"
        if self.schema:
            col_descs = []
            for c in self.schema.columns:
                desc = c.name
                details = []
                if c.data_type and c.data_type != "text":
                    details.append(c.data_type)
                if c.formatting:
                    details.append(f"formatting: {c.formatting}")
                if c.description:
                    # Access description - keep it brief if needed
                    pass 
                
                if details:
                    desc += f" ({', '.join(details)})"
                col_descs.append(desc)
            cols = ", ".join(col_descs)
            
        # Build constraint string for prompt
        constraint_str = ""
        if self.constraints:
            constraint_str = f"Constraints: {json.dumps(self.constraints)}"
            
        estimate_str = ""
        if self.estimated_rows and self.estimated_rows > 0:
            estimate_str = f"Estimated Target Rows: ~{self.estimated_rows}. You MUST extract them all."

        prompt = DATA_EXTRACTION_PROMPT.format(
            query=self.query,
            constraint_str=constraint_str,
            estimate_str=estimate_str,
            source=self.source
        )
        
        if self.partition_key and self.partition_value:
            partition_estimate_str = (
                f"Expected rows in this partition: ~{self.estimated_rows}.\n"
                if self.estimated_rows else ""
            )
            prompt += DATA_EXTRACTION_PARTITION_CONTEXT.format(
                partition_key=self.partition_key,
                partition_value=self.partition_value,
                partition_estimate_str=partition_estimate_str
            )
        
        prompt += DATA_EXTRACTION_COLUMNS_SUFFIX.format(cols=cols)
        return prompt

@dataclass
class QueryAnalysisResult:
    rewritten_query: str
    estimated_rows: int
    requires_decomposition: bool
    reasoning: str
    task: Optional["DataTask"] = None

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
        Converts raw query into a structured DataTask.
        """
        prompt = QUERY_ANALYSIS_PROMPT.format(query=query)
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
        
        # Propagate estimate to task for Completeness Check downstream

        # 3. Determine Decomposition
        # The Analyzer now only provides the estimate. The logic is in DataPlanBuilder.
        # We perform a basic check here just for the 'reasoning' field if needed, or return defaults.
        requires_decomposition = False 
        reasoning = "Decomposition decision deferred to Planner."
        
        logger.info(f"Analysis Complete. Rows: {estimated_rows}")

        return QueryAnalysisResult(
            rewritten_query=rewritten_query,
            estimated_rows=estimated_rows,
            requires_decomposition=requires_decomposition, # Placeholder
            reasoning=reasoning,
            task=task
        )

    async def _rewrite_query(self, query: str) -> str:
        prompt = QUERY_REWRITE_PROMPT.format(query=query)
        response = await self.llm.agenerate(prompt, temperature=0.0)
        return response.content.strip().strip('"')

    async def _estimate_row_count(self, query: str) -> int:
        prompt = ROW_ESTIMATION_PROMPT.format(query=query)
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

    async def should_decompose(self, query: str, estimated_rows: int) -> Tuple[bool, str]:
        """
        Asks LLM if Map-Reduce decomposition is necessary for this query.
        """
        prompt = DECOMPOSITION_DECISION_PROMPT.format(query=query, estimated_rows=estimated_rows)
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
