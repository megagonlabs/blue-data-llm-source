import logging
import pandas as pd
from typing import List, Optional, Tuple
from utils.llm_utils import LLMClient, LLMMetrics
from src.dataplan import DataPlanBuilder
from src.engine import MapReduceEngine
from src.validator import Validator

logger = logging.getLogger(__name__)

class PKDB:
    """
    Parametric Knowledge Database (PKDB) Client.
    High-level interface for Auto-Partitioned Extraction.
    """
    
    def __init__(
        self, 
        model: str = "gpt-4o-mini", 
        cache_dir: Optional[str] = ".cache_pkdb", 
        max_concurrent: int = 5, 
        use_cache: bool = True, 
        max_partitions: int = 50,
        num_schema_candidates: int = 3,
        schema_temperature: float = 0.7,
        extraction_temperature: float = 0.0,
        decomposition_strategy: str = "rule_based",
        row_threshold: int = 50,
        max_depth: int = 1,
        partitioning_method: str = "semantic"
    ):
        self.llm = LLMClient(model=model, cache_dir=cache_dir, default_use_cache=use_cache)
        self.decomposition_strategy = decomposition_strategy
        self.row_threshold = row_threshold
        self.max_depth = max_depth
        self.partitioning_method = partitioning_method
        
        self.builder = DataPlanBuilder(
            self.llm, 
            max_partitions=max_partitions,
            num_candidates=num_schema_candidates,
            temperature=schema_temperature
        )
        self.engine = MapReduceEngine(
            self.llm, 
            max_concurrent=max_concurrent,
            extraction_temperature=extraction_temperature
        )
        self.validator = Validator()
        
    async def extract_table(self, query: str, strategy: str = "llm_rank") -> Tuple[pd.DataFrame, LLMMetrics, int]:
        """
        Main API: Extracts a table from the LLM's parametric knowledge
        using the Map-Reduce Auto-Partitioning strategy.
        
        Args:
            query: The user query, e.g. "List all US cities"
            strategy: Schema selection strategy ('first', 'llm_rank')
            
        Returns:
            (pd.DataFrame, LLMMetrics, int): The extracted table, usage metrics, and subquery count.
        """
        # 1. Plan
        plan = await self.builder.create_plan(
            query, 
            strategy=strategy,
            decomposition_strategy=self.decomposition_strategy,
            partitioning_method=self.partitioning_method
        )
        
        # 2. Execute
        raw_df, metrics = await self.engine.execute(plan)
        
        # 3. Validate/Clean
        final_df = self.validator.clean_data(raw_df, primary_keys=plan.schema.primary_keys)
        
        return final_df, metrics, len(plan.map_tasks)
