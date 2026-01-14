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
    
    def __init__(self, model: str = "gpt-4o", cache_dir: str = ".cache_pkdb"):
        self.llm = LLMClient(model=model, cache_dir=cache_dir)
        self.builder = DataPlanBuilder(self.llm)
        self.engine = MapReduceEngine(self.llm)
        self.validator = Validator()
        
    async def extract_table(self, query: str) -> Tuple[pd.DataFrame, LLMMetrics, int]:
        """
        Main API: Extracts a table from the LLM's parametric knowledge
        using the Map-Reduce Auto-Partitioning strategy.
        
        Args:
            query: The user query, e.g. "List all US cities"
            
        Returns:
            (pd.DataFrame, LLMMetrics, int): The extracted table, usage metrics, and subquery count.
        """
        # 1. Plan
        plan = await self.builder.create_plan(query)
        
        # 2. Execute
        raw_df, metrics = await self.engine.execute(plan)
        
        # 3. Validate/Clean
        final_df = self.validator.clean_data(raw_df, primary_keys=plan.schema.primary_keys)
        
        return final_df, metrics, len(plan.map_tasks)
