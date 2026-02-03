import logging
import pandas as pd
from typing import List, Optional, Tuple
from utils.llm_utils import LLMClient, LLMMetrics
from src.dataplan import DataPlanBuilder
from src.engine import MapReduceEngine
from src.validator import Validator
from src.code_validator import CodeValidator

logger = logging.getLogger(__name__)

from dataclasses import dataclass, field

@dataclass
class PKDBConfig:
    """Centralized Configuration for PKDB."""
    model: str = "gpt-4o"
    cache_dir: str = ".cache_pkdb"
    max_concurrent: int = 5
    use_cache: bool = True
    max_partitions: int = 50
    num_schema_candidates: int = 3
    schema_temperature: float = 0.7
    extraction_temperature: float = 0.0
    decomposition_strategy: str = "rule_based"
    row_threshold: int = 120 # [Optimized Default V4]
    max_depth: int = 1
    partitioning_method: str = "smart"
    max_total_requests: int = 10000
    default_execution_mode: str = "batch"

class PKDB:
    """
    Parametric Knowledge Database (PKDB) Client.
    High-level interface for Auto-Partitioned Extraction.
    """
    
    def __init__(
        self, 
        config: Optional[PKDBConfig] = None,
        **kwargs # Backward compatibility for loose args
    ):
        # 1. Resolve Config
        if config is None:
            # Check if kwargs are populated, else use defaults
            # Filter kwargs to only those matching PKDBConfig fields
            valid_keys = PKDBConfig.__annotations__.keys()
            filtered_args = {k: v for k, v in kwargs.items() if k in valid_keys}
            self.config = PKDBConfig(**filtered_args)
        else:
            self.config = config
            
        # 2. Initialize Components
        self.llm = LLMClient(
            model=self.config.model, 
            cache_dir=self.config.cache_dir, 
            default_use_cache=self.config.use_cache, 
            max_total_requests=self.config.max_total_requests,
            shared_state=kwargs.get('shared_state') # Special case: Runtime object, not config
        )
        
        self.builder = DataPlanBuilder(
            self.llm, 
            max_partitions=self.config.max_partitions,
            num_candidates=self.config.num_schema_candidates,
            temperature=self.config.schema_temperature
        )
        self.engine = MapReduceEngine(
            self.llm, 
            extraction_temperature=self.config.extraction_temperature
        )
        self.validator = Validator()
        self.code_validator = CodeValidator(self.llm)
        
    @property
    def row_threshold(self):
        return self.config.row_threshold
        
    @property
    def decomposition_strategy(self):
        return self.config.decomposition_strategy
        
    @property
    def partitioning_method(self):
        return self.config.partitioning_method

    @property
    def default_execution_mode(self):
        return self.config.default_execution_mode
        
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
