import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import asyncio

from src.schema import SchemaDefinition, SchemaGenerator, Column
from utils.llm_utils import LLMClient

logger = logging.getLogger(__name__)

@dataclass
class MapTask:
    """Represents a single partition query to be executed"""
    id: str
    partition_key: str
    partition_value: str
    base_query: str
    target_columns: List[str]
    
    def to_prompt(self) -> str:
        cols = ", ".join(self.target_columns)
        return f"""
        List all "{self.partition_key}" is "{self.partition_value}" for "{self.base_query}".
        Extract the following columns: {cols}.
        Return as JSON object with key "items": {{"items": [...]}}.
        """

@dataclass
class DataPlanRequest:
    query: str
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
    
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.schema_gen = SchemaGenerator(llm_client)
        
    async def create_plan(self, query: str) -> DataPlanRequest:
        # 1. Infer Schema
        logger.info(f"Inferring schema for query: {query}")
        schema = await self.schema_gen.infer_schema(query)
        logger.info(f"Inferred schema: {schema.table_name} with columns {[c.name for c in schema.columns]}")
        
        # 2. Identify Assignments (Partition Strategy)
        partition_keys = schema.get_partition_keys()
        
        map_tasks = []
        
        if not partition_keys:
            # Fallback: Single Task (Flat)
            logger.info("No partition keys found. Creating single flat task.")
            map_tasks.append(MapTask(
                id="task_flat_0",
                partition_key="All",
                partition_value="All",
                base_query=query,
                target_columns=[c.name for c in schema.columns]
            ))
        else:
            # Greedy: Pick the first good partition key
            # TODO: Advanced multi-key partitioning?
            pkey = partition_keys[0]
            logger.info(f"Partitioning by key: {pkey.name}")
            
            values = await self._fetch_partition_values(query, pkey)
            logger.info(f"Found {len(values)} partition values (e.g., {values[:3]})")
            
            for i, val in enumerate(values):
                map_tasks.append(MapTask(
                    id=f"task_{pkey.name}_{i}",
                    partition_key=pkey.name,
                    partition_value=val,
                    base_query=query,
                    target_columns=[c.name for c in schema.columns]
                ))
                
        return DataPlanRequest(
            query=query,
            schema=schema,
            map_tasks=map_tasks
        )
        
    async def _fetch_partition_values(self, query: str, key: Column) -> List[str]:
        """
        Ask LLM to list all values for the partition key.
        e.g. "List all States"
        """
        prompt = f"""
        Reference Query: "{query}"
        Target Attribute: "{key.name}" ({key.description})
        
        List ALL possible values for "{key.name}" that are relevant to the query.
        For example, if key is "State", list all 50 US states.
        If key is "Year" for "Movies in 2020s", list 2020, 2021...
        
        Return ONLY a simple list of strings, one per line. No numbering.
        """
        
        response = await self.llm.agenerate(prompt, temperature=0.0)
        
        # Parse lines
        values = []
        for line in response.content.split("\n"):
            clean = line.strip("-* ").strip()
            if clean and len(clean) < 100: # Sanity check
                values.append(clean)
                
        return values
