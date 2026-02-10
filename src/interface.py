import logging
import pandas as pd
from typing import List, Optional, Tuple
from utils.llm_utils import LLMClient, LLMMetrics
from src.dataplan import DataPlanBuilder
from src.engine import MapReduceEngine
from src.schema import SchemaDefinition, SchemaGenerator

logger = logging.getLogger(__name__)

from dataclasses import dataclass, field

@dataclass
class PKDBConfig:
    """Centralized Configuration for PKDB."""
    model: str = "gpt-4o"
    cache_dir: str = ".cache_pkdb"
    max_concurrent: int = 5
    use_cache: bool = True
    max_partitions: int = 200
    num_schema_candidates: int = 3
    schema_temperature: float = 0.7
    extraction_temperature: float = 0.0
    decomposition_strategy: str = "rule_based"
    row_threshold: int = 120 # [Optimized Default V4.2: Reverted to 120 for best F1/Cost balance]
    max_depth: int = 2
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
        
        # Validators (Configurable)
        from src.validator import GlobalValidator
        from src.schema import SchemaDefinition, SchemaGenerator
        from src.dataplan import DataPlanBuilder # Re-import for clarity, though already there
        from src.engine import MapReduceEngine # Re-import for clarity, though already there
        from src.schema import SchemaDefinition # Needed for new extract_table signature
        self.map_validator = GlobalValidator(self.llm, strategies=getattr(self.config, 'map_validators', ["ChunkConstraints", "FormatNormalization"]))
        self.global_validator = GlobalValidator(self.llm, strategies=getattr(self.config, 'global_validators', ["Deduplication", "EntityResolution", "PrimaryKeyValidation", "Completeness", "FormatNormalization"]))
        
        self.schema_generator = SchemaGenerator(
            self.llm, 
            num_candidates=self.config.num_schema_candidates,
            temperature=self.config.schema_temperature
        )
        
        self.planner = DataPlanBuilder(
            llm_client=self.llm,
            max_partitions=self.config.max_partitions,
            num_candidates=self.config.num_schema_candidates,
            temperature=self.config.schema_temperature,
            row_threshold=self.config.row_threshold,
            decomposition_strategy=self.config.decomposition_strategy, 
            partitioning_method=self.config.partitioning_method
        )
        
        # V5: Unified GlobalValidator
        # We can configure sub-validators here
        self.validator = self.global_validator
        
        # Inject map_validator into Engine
        # For now, map_validator can be the same GlobalValidator instance or a lightweight one
        # Let's use the same instance, but GlobalValidator logic differentiates based on phase?
        # Actually, let's create a separate lightweight one if needed, or share.
        # Sharing is fine as it's stateless mostly.
        # self.map_validator is already initialized above with 'map_validators' config strategy
        # Keeping them separate allows different strategies for Map vs Reduce phases.
        # self.map_validator = self.validator  # REMOVED overwrite 
        
        self.engine = MapReduceEngine(
            llm_client=self.llm, 
            planner=self.planner,
            max_partitions=self.config.max_partitions,
            default_execution_mode=self.config.default_execution_mode,
            map_validator=self.map_validator
        )
        
        self.code_validator = None # Deprecated, integrated into GlobalValidator = None # Deprecated
        self.builder = None # Deprecated
        
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
        schema: Optional[SchemaDefinition] = None,
        context: dict = None,
        execution_mode: Optional[str] = None,
        validator = None
    ) -> Tuple[pd.DataFrame, LLMMetrics, int]:
        """
        Main API: Extracts a table from the LLM's parametric knowledge.
        
        Args:
            query: The user query, e.g. "List all US cities"
            strategy: Schema selection strategy ('first', 'llm_rank')
            context: Context dictionary (e.g. output_dir for artifacts)
            schema: Optional strict SchemaDefinition to enforce (skips inference)
            execution_mode: "batch" or "row_by_row" (defaults to self.default_execution_mode)
            validator: Optional GlobalValidator instance.
            
        Returns:
            (pd.DataFrame, LLMMetrics, int): The extracted table, usage metrics, and subquery count.
        """
        context = context or {}
        mode = execution_mode or self.default_execution_mode
        
        # 1. Plan
        plan = await self.planner.create_plan(
            query, 
            decomposition_strategy=self.config.decomposition_strategy, # Fixed attribute access
            partitioning_method=self.config.partitioning_method,
            context=context,
            schema=schema,
            execution_mode=mode,
            row_threshold=self.config.row_threshold,
            max_depth=self.config.max_depth
        )
        
        # 2. Execute
        val = validator if validator else self.map_validator

        # Re-instantiate engine with validator? 
        # Or just use the existing self.engine if no validator passed?
        # But self.engine was init with self.map_validator.
        # If validator is passed, we MUST create a new engine instance or update it.
        # Creating new lightweight engine is safer.
        
        engine = MapReduceEngine(
            self.llm, 
            planner=self.planner, 
            max_partitions=self.config.max_partitions,
            map_validator=val,
            global_validator=self.global_validator
        )
        
        df, metrics = await engine.execute(plan, context=context)
        
        # 3. Schema Inference is implicit in Plan or done after?
        # In this version, Plan includes Schema.
        
        return df, metrics, len(plan.map_tasks)
        target_schema = await self.schema_generator.infer_schema(
             query, 
             strategy=strategy,
             context={"output_dir": "."}, # TODO: handle context better
             input_schema=schema 
        )
        
        # 3. Validate/Clean
        # Enrich context for validators
        val_context = context.copy()
        if plan.schema:
            val_context['schema'] = plan.schema
            val_context['primary_keys'] = plan.schema.primary_keys
            
        final_df = await self.validator.run(
            raw_df, 
            context=val_context
        )
        
        # 4. Code-Based Validation is now integrated into GlobalValidator
        
        return final_df, metrics, len(plan.map_tasks)
