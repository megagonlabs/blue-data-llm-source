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
    DECOMPOSITION_DECISION_PROMPT
)

logger = logging.getLogger(__name__)

@dataclass
class DataTask:
    """
    Architecture V3: A Unified structured representation of an extraction goal (Node in Execution Graph).
    """
    # 1. Goal Definition
    query: str  # Natural language query
    constraints: dict = field(default_factory=dict) # e.g. {"Year": "2023"}
    collection_strategy: str = "ALL"  # ALL, LIMIT <N>, MOST_COMMON
    
    # 2. Context & Lineage
    schema: Optional[SchemaDefinition] = None  # The expected output structure (V3)
    parent: Optional["DataTask"] = None        # Pointer to creator task (V3)
    trace_id: str = field(default_factory=lambda: hashlib.md5(str(time.time()).encode()).hexdigest())
    
    # 3. Partitioning Context (If this is a leaf/partition node)
    partition_key: Optional[str] = None   # e.g. "Year"
    partition_value: Optional[str] = None # e.g. "2023"
    estimated_rows: Optional[int] = None # Estimated total rows (for completeness check)
    
    # 4. Execution Config
    execution_mode: str = "batch" # "batch" or "row_by_row"
    
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
            cols = ", ".join([c.name for c in self.schema.columns])
            
        # Build constraint string for prompt
        constraint_str = ""
        if self.constraints:
            constraint_str = f"Constraints: {json.dumps(self.constraints)}"
            
        estimate_str = ""
        if self.estimated_rows and self.estimated_rows > 0:
            estimate_str = f"Estimated Target Rows: ~{self.estimated_rows}. You MUST extract them all."

        prompt = f"""
        User Request: "{self.query}"
        Target Scope:
        {constraint_str}
        {estimate_str}

        **CRITICAL INSTRUCTION**: Extract ALL data matching the request.
        You must be as comprehensive as possible. If the table usually has many rows, return MANY rows.
        
        **OUTPUT FORMAT**:
        Return ONLY the raw JSON list (or CSV) containing the data. 
        DO NOT include any text like "Here is the table" or markdown code blocks.
        Just the raw data.
        """
        
        if self.partition_key and self.partition_value:
            prompt += f"""
        You are processing a PARTITION: "{self.partition_key}" = "{self.partition_value}".
        You MUST ONLY return items where "{self.partition_key}" is equal to "{self.partition_value}".
        """
        
        prompt += f"""
        Extract the following columns: {cols}.
        Return as JSON object with key "items": {{"items": [...]}}.
        """
        return prompt

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
        
        # [REVERTED] Propagate estimate to task for Completeness Check downstream
        # task.estimated_rows = estimated_rows

        # 3. Determine Decomposition
        if decomposition_strategy == "llm_based":
            # Hybrid approach: If rows > 100, force decomposition to avoid truncation
            if estimated_rows > 100:
                logger.info(f"Estimated rows ({estimated_rows}) > 100. Forcing Map-Reduce to prevent LLM truncation.")
                requires_decomposition = True
                reasoning = "Forced decomposition due to high row count."
            else:
                requires_decomposition, reasoning = await self._decide_decomposition_llm(rewritten_query, estimated_rows)
        else:
            # Rule based default
            # Check strategy too? If LIMIT 5, maybe flat is fine?
            requires_decomposition = estimated_rows > row_threshold
            reasoning = f"Estimated rows ({estimated_rows}) > Threshold ({row_threshold})"
            
            if "LIMIT" in task.collection_strategy and estimated_rows < row_threshold * 2:
                # If limited and small-ish, keep flat
                pass # Logic holds

        logger.info(f"Decomposition Assessment: {requires_decomposition} ({reasoning})")

        return QueryAnalysisResult(
            rewritten_query=rewritten_query,
            estimated_rows=estimated_rows,
            requires_decomposition=requires_decomposition,
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

    async def _decide_decomposition_llm(self, query: str, estimated_rows: int) -> Tuple[bool, str]:
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
