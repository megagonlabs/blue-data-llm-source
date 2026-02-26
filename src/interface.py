import logging
import pandas as pd
from typing import List, Optional, Tuple
from utils.llm_utils import LLMClient, LLMMetrics
from src.dataplan import DataPlanBuilder
from src.engine import MapReduceEngine
from src.schema import SchemaDefinition, SchemaGenerator
from src.validator import DataValidator
logger = logging.getLogger(__name__)

from dataclasses import dataclass, field

@dataclass
class PKDBConfig:
    """Centralized Configuration for PKDB."""
    model: str = "gpt-4o"
    cache_dir: str = ".cache_pkdb"
    max_concurrent: int = 15
    use_cache: bool = True
    max_partitions: int = 200
    num_schema_candidates: int = 3
    schema_temperature: float = 0.7
    extraction_temperature: float = 0.0
    decomposition_strategy: str = "llm_based"
    row_threshold: int = 120
    max_depth: int = 2
    partitioning_method: str = "llm_based"
    partition_selection_mode: str = "llm" # "llm" or "scoring"
    enable_heuristic_scoring: bool = False
    max_total_requests: int = 10000
    default_execution_mode: str = "batch"
    source: str = "Wikipedia" # Configurable Data Source

    # Continuation / Completeness
    continuation_pk_str_limit: int = 50  # Max chars per PK value in continuation prompt

    # Validation
    map_validators: List[str] = field(default_factory=lambda: ["ChunkConstraints", "FormatNormalization"])
    global_validators: List[str] = field(default_factory=lambda: ["Deduplication", "EntityResolution", "PrimaryKeyValidation", "Completeness", "FormatNormalization"])
    dedup_strategy: str = "llm_based"  # "first" or "llm_based" (conflict resolution for duplicate rows)

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
        self.map_validator = DataValidator(
            self.llm,
            strategies=getattr(self.config, 'map_validators', ["ChunkConstraints", "FormatNormalization"]),
            dedup_strategy=self.config.dedup_strategy
        )
        self.global_validator = DataValidator(
            self.llm,
            strategies=getattr(self.config, 'global_validators', ["Deduplication", "EntityResolution", "PrimaryKeyValidation", "Completeness", "FormatNormalization"]),
            dedup_strategy=self.config.dedup_strategy
        )
        
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
            partitioning_method=self.config.partitioning_method,
            partition_selection_mode=self.config.partition_selection_mode,
            enable_heuristic_scoring=self.config.enable_heuristic_scoring
        )
        
        self.engine = MapReduceEngine(
            llm_client=self.llm,
            planner=self.planner,
            max_partitions=self.config.max_partitions,
            default_execution_mode=self.config.default_execution_mode,
            map_validator=self.map_validator,
            global_validator=self.global_validator,
            continuation_pk_str_limit=self.config.continuation_pk_str_limit
        )
        
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
        map_validator = None,
        global_validator = None,
        plan_only: bool = False # Dry run for planning cost
    ) -> Tuple[pd.DataFrame, LLMMetrics, int]:
        """
        Main API: Extracts a table from the LLM's parametric knowledge.
        
        Args:
            query: The user query, e.g. "List all US cities"
            strategy: Schema selection strategy ('first', 'llm_rank')
            context: Context dictionary (e.g. output_dir for artifacts)
            schema: Optional strict SchemaDefinition to enforce (skips inference)
            execution_mode: "batch" or "row_by_row" (defaults to self.default_execution_mode)
            map_validator: Optional Map Side Validator override.
            global_validator: Optional Reduce Side Validator override.
            
        Returns:
            (pd.DataFrame, LLMMetrics, int): The extracted table, usage metrics, and subquery count.
        """
        context = context or {}
        mode = execution_mode or self.default_execution_mode
        
        # 1. Plan
        plan = await self.planner.create_plan(
            query, 
            decomposition_strategy=self.config.decomposition_strategy,
            partitioning_method=self.config.partitioning_method,
            context=context,
            schema=schema,
            execution_mode=mode,
            row_threshold=self.config.row_threshold,
            max_depth=self.config.max_depth,
            source=self.config.source
        )
        
        if plan_only:
            logger.info("Plan-only mode enabled. Skipping execution.")
            # Retrieve metrics from llm client manually since engine won't run
            metrics = self.llm.get_cumulative_metrics()
            return pd.DataFrame(), metrics, len(plan.map_tasks)
        
        # 2. Execute
        m_val = map_validator if map_validator else self.map_validator
        g_val = global_validator if global_validator else self.global_validator
        
        engine = MapReduceEngine(
            self.llm, 
            planner=self.planner, 
            max_partitions=self.config.max_partitions,
            map_validator=m_val,
            global_validator=g_val
        )
        
        df, metrics = await engine.execute(plan, context=context)
        
        return df, metrics, len(plan.map_tasks)

