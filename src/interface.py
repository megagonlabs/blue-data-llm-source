import logging
import pandas as pd
from typing import List, Optional, Tuple
from utils.llm_utils import LLMClient, LLMMetrics
from src.dataplan import DataPlanBuilder
from src.engine import MapReduceEngine
from src.validator import Validator
from src.code_validator import CodeValidator

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
        partitioning_method: str = "semantic",
        max_total_requests: int = 10000,
        shared_state: Optional[dict] = None,
        default_execution_mode: str = "batch"
    ):
        self.llm = LLMClient(
            model=model, 
            cache_dir=cache_dir, 
            default_use_cache=use_cache, 
            max_total_requests=max_total_requests,
            shared_state=shared_state
        )
        self.decomposition_strategy = decomposition_strategy
        self.row_threshold = row_threshold
        self.max_depth = max_depth
        self.partitioning_method = partitioning_method
        self.default_execution_mode = default_execution_mode
        
        self.builder = DataPlanBuilder(
            self.llm, 
            max_partitions=max_partitions,
            num_candidates=num_schema_candidates,
            temperature=schema_temperature
        )
        self.engine = MapReduceEngine(
            self.llm, 
            extraction_temperature=extraction_temperature
        )
        self.validator = Validator()
        self.code_validator = CodeValidator(self.llm)
        
    async def extract_table(
        self, 
        query: str, 
        strategy: str = "llm_rank", 
        context: dict = None,
        schema: Optional['SchemaDefinition'] = None,
        execution_mode: Optional[str] = None
    ) -> Tuple[pd.DataFrame, LLMMetrics, int]:
        """
        Main API: Extracts a table from the LLM's parametric knowledge
        using the Map-Reduce Auto-Partitioning strategy.
        
        Args:
            query: The user query, e.g. "List all US cities"
            strategy: Schema selection strategy ('first', 'llm_rank')
            context: Context dictionary (e.g. output_dir for artifacts)
            schema: Optional strict SchemaDefinition to enforce (skips inference)
            execution_mode: "batch" or "row_by_row" (defaults to self.default_execution_mode)
            
        Returns:
            (pd.DataFrame, LLMMetrics, int): The extracted table, usage metrics, and subquery count.
        """
        context = context or {}
        mode = execution_mode or self.default_execution_mode
        
        # 1. Plan
        plan = await self.builder.create_plan(
            query, 
            decomposition_strategy=self.decomposition_strategy,
            partitioning_method=self.partitioning_method,
            context=context,
            schema=schema,
            execution_mode=mode,
            row_threshold=self.row_threshold
        )
        
        # 2. Execute
        raw_df, metrics = await self.engine.execute(plan, context=context)
        
        # 3. Validate/Clean
        final_df = await self.validator.clean_data(
            raw_df, 
            primary_keys=plan.schema.primary_keys, 
            context=context
        )
        
        # 4. Code-Based Validation (Gen-T Assertions)
        if not final_df.empty and plan.schema:
            logger.info("Running Code-Based Validation...")
            try:
                # Generate schema description for context
                schema_desc = f"Table: {plan.schema.table_name}\nColumns: {', '.join([c.name for c in plan.schema.columns])}"
                
                records = final_df.to_dict('records')
                validated_records = await self.code_validator.run(records, schema_desc)
                final_df = pd.DataFrame(validated_records)
            except Exception as e:
                logger.error(f"Code Validation failed: {e}")
        
        return final_df, metrics, len(plan.map_tasks)
