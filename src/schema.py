import json
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
import hashlib

from utils.llm_utils import LLMClient
from src.prompts import (
    SCHEMA_POOL_PROMPT,
    SCHEMA_CANDIDATES_PROMPT,
    SCHEMA_RANKING_PROMPT,
    SCHEMA_TYPE_INFERENCE_PROMPT
)

logger = logging.getLogger(__name__)

@dataclass
class Column:
    name: str
    description: str = ""
    semantic_type: str = "text" # text, categorical, numerical, time, entity
    data_type: str = "text" # [NEW] V3: Underlying type (int, float, date, text)
    formatting: str = "" # [NEW] V3: Format instructions (e.g. "YYYY-MM-DD")
    is_partition_key: bool = False
    
    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "semantic_type": self.semantic_type,
            "data_type": self.data_type,
            "formatting": self.formatting,
            "is_partition_key": self.is_partition_key
        }

@dataclass
class SchemaDefinition:
    table_name: str
    columns: List[Column]
    primary_keys: List[str] = field(default_factory=list)
    # partition_strategy removed in Architecture V3 (Decoupled)
    
    def to_dict(self):
        return {
            "table_name": self.table_name,
            "columns": [c.to_dict() for c in self.columns],
            "primary_keys": self.primary_keys
        }
        
    def get_partition_keys(self) -> List[Column]:
        return [c for c in self.columns if c.is_partition_key]

class SchemaGenerator:
    """
    Induces a schema from a user query using LLM.
    Implements efficient table generation techniques:
    1. Candidate Generation
    2. Candidate Ranking
    3. Type Inference
    """
    
    def __init__(self, llm_client: LLMClient, num_candidates: int = 3, temperature: float = 0.7):
        self.llm = llm_client
        self.default_num_candidates = num_candidates
        self.default_temperature = temperature
        
    async def infer_schema(self, query: str, num_candidates: Optional[int] = None, strategy: str = "llm_rank", context: dict = None) -> SchemaDefinition:
        """
        Main entry point: given a query like "List all US cities",
        infer the best schema.
        Phase 2 Update:
        1. Generate Global Schema Pool (Hidden/Candidate Columns)
        2. Generate Schema Candidates (selecting from pool)
        3. Rank & Refine
        """
        context = context or {}
        
        # Step 1: Generate Global Schema Pool of latent attributes
        global_pool = await self.generate_pool(query)
        logger.info(f"Generated global schema pool: {global_pool}")
        
        # Step 2: Generate Schema Candidates using the pool
        k = num_candidates or self.default_num_candidates
        candidates = await self.generate_candidates(query, k, global_pool)
        
        if not candidates:
            # Fallback
            return self._create_fallback_schema(query)         
        if strategy == "first":
            best_candidate = candidates[0]
        else:
            best_candidate = await self._rank_candidates(query, candidates)
        
        # Step 3: Refine types and keys
        final_schema = await self.infer_types_and_keys(query, best_candidate)
        
        # Artifact Persistence
        if "output_dir" in context:
            import os
            try:
                schema_artifact = {
                    "query": query,
                    "global_pool": global_pool,
                    "candidates": candidates,
                    "selected_strategy": strategy,
                    "final_schema": final_schema.to_dict()
                }
                with open(os.path.join(context["output_dir"], "schema.json"), "w") as f:
                    json.dump(schema_artifact, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to save schema artifact: {e}")
                
        return final_schema

    async def generate_pool(self, query: str) -> List[str]:
        """
        Phase 2.1: Generate a pool of ~15 potential attributes.
        Include 'hidden' attributes good for partitioning (Time, Location, Category).
        """
        prompt = SCHEMA_POOL_PROMPT.format(query=query)
        response = await self.llm.agenerate(prompt, temperature=0.7, response_format={"type": "json_object"})
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            data = json.loads(content)
            # Handle {"columns": [...]} or just [...]
            if isinstance(data, dict):
                return data.get("columns", data.get("attributes", []))
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"Failed to generate column pool: {e}")
            return []

    async def generate_candidates(self, query: str, k: int, column_pool: List[str] = []) -> List[Dict]:
        pool_str = ", ".join(column_pool)
        prompt = SCHEMA_CANDIDATES_PROMPT.format(query=query, pool_str=pool_str, k=k)
        
        response = await self.llm.agenerate(prompt, temperature=self.default_temperature, response_format={"type": "json_object"})
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            logger.info(f"Raw Schema Candidates JSON: {content}")
            
            candidates = json.loads(content)
            
            # Robust extraction of schemas list
            schemas = []
            if isinstance(candidates, dict) and "schemas" in candidates:
                schemas = candidates["schemas"]
            elif isinstance(candidates, list):
                schemas = candidates
                
            # Filter valid schemas
            valid_schemas = []
            for s in schemas:
                # Normalize keys to lowercase
                s = {k.lower(): v for k, v in s.items()}
                # Also normalize keys inside columns if needed (not strictly needed but good practice)
                if "columns" in s and isinstance(s["columns"], list):
                    new_cols = []
                    for c in s["columns"]:
                        if isinstance(c, dict):
                            new_cols.append({k.lower(): v for k, v in c.items()})
                    s["columns"] = new_cols

                if isinstance(s, dict) and "columns" in s and "table_name" in s:
                    valid_schemas.append(s)
                    
            return valid_schemas
        except Exception as e:
            logger.error(f"Failed to parse schema candidates: {e}")
            return []

    async def _rank_candidates(self, query: str, candidates: List[Dict]) -> Dict:
        if len(candidates) == 1:
            return candidates[0]
            
        # LLM-as-a-Judge ranking
        candidates_str = json.dumps(candidates, indent=2)
        prompt = SCHEMA_RANKING_PROMPT.format(query=query, num_candidates=len(candidates), candidates_str=candidates_str)
        response = await self.llm.agenerate(prompt, temperature=0.0)
        try:
            # Extract first number
            idx = int("".join(filter(str.isdigit, response.content.strip()))[0])
            if 0 <= idx < len(candidates):
                return candidates[idx]
        except:
            logger.warning("Ranking parsing failed, selecting first candidate.")
            
        return candidates[0]

    async def infer_types_and_keys(self, query: str, schema_dict: Dict) -> SchemaDefinition:
        # Determine semantic types and partition suitability
        if 'columns' not in schema_dict:
            logger.error(f"Schema dict missing 'columns': {schema_dict}")
            return self._create_fallback_schema(query)
            
        columns_str = ", ".join([c.get('name', 'Unknown') for c in schema_dict['columns']])
        table_name = schema_dict.get('table_name', 'data')
        
        prompt = SCHEMA_TYPE_INFERENCE_PROMPT.format(query=query, table_name=table_name, columns_str=columns_str)
        
        response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
        type_info = {}
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            type_info = json.loads(content).get("columns", {})
        except Exception as e:
            logger.error(f"Type inference failed: {e}")

        cols = []
        for c_dict in schema_dict['columns']:
            name = c_dict['name']
            info = type_info.get(name, {})
            cols.append(Column(
                name=name,
                description=c_dict.get('description', ''),
                semantic_type=info.get('type', 'text'),
                is_partition_key=info.get('is_partition', False)
            ))
            
        return SchemaDefinition(
            table_name=schema_dict.get('table_name', 'extracted_data'),
            columns=cols,
            primary_keys=schema_dict.get('primary_keys', [])
        )
        
    def _create_fallback_schema(self, query: str) -> SchemaDefinition:
        return SchemaDefinition(
            table_name="data",
            columns=[Column(name="Content", description="Extracted content", is_partition_key=False)]
        )
