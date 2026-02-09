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
    
    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "semantic_type": self.semantic_type,
            "data_type": self.data_type,
            "formatting": self.formatting
        }

@dataclass
class SchemaDefinition:
    table_name: str
    columns: List[Column]
    primary_keys: List[str] = field(default_factory=list)
    
    def to_dict(self):
        return {
            "table_name": self.table_name,
            "columns": [c.to_dict() for c in self.columns],
            "primary_keys": self.primary_keys
        }

        


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
        
    async def infer_schema(self, query: str, num_candidates: Optional[int] = None, strategy: str = "llm_rank", context: dict = None, input_schema: Optional[SchemaDefinition] = None) -> SchemaDefinition:
        """
        Main entry point.
        Process:
        1. [Optional] If input_schema provided:
           - Skip Generation (Steps 1-3).
           - Jump to Step 4 (Enrichment) if missing metadata.
        2. Step 1: Generate Pool.
        3. Step 2: Generate Candidates.
        4. Step 3: Rank.
        5. Step 4: Enrich (Description, Types).
        """
        context = context or {}
        
        # --- PATH A: BYPASS (Schema Provided) ---
        if input_schema:
            logger.info(f"Schema provided for '{query}'. Skipping generation.")
            # Verify if enrichment is needed (if descriptions/types are empty)
            needs_enrichment = any(not c.description or not c.semantic_type for c in input_schema.columns)
            
            if needs_enrichment:
                logger.info("Provided schema lacks metadata. Running Enrichment Step.")
                # Enrich operates on dict representation internally for now, or we adapt
                # Creating a dummy 'best_candidate' dict from the schema object
                candidate_dict = {
                    "table_name": input_schema.table_name,
                    "columns": [c.to_dict() for c in input_schema.columns],
                    "primary_keys": input_schema.primary_keys
                }
                final_schema = await self.infer_enrichment(query, candidate_dict)
                return final_schema
            else:
                return input_schema

        # --- PATH B: GENERATION (CoT) ---
        
        # Step 1: Pool
        global_pool = await self.generate_pool(query)
        logger.info(f"Generated global schema pool: {global_pool}")
        
        # Step 2: Candidates
        k = num_candidates or self.default_num_candidates
        candidates = await self.generate_candidates(query, k, global_pool)
        
        if not candidates:
            return self._create_fallback_schema(query)         
            
        # Step 3: Rank
        if strategy == "first":
            best_candidate = candidates[0]
        else:
            best_candidate = await self._rank_candidates(query, candidates)
        
        # Step 4: Enrich (Replaces old 'infer_types_and_keys')
        final_schema = await self.infer_enrichment(query, best_candidate)
        
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
                pass
                
        return final_schema

    async def infer_enrichment(self, query: str, schema_dict: Dict) -> SchemaDefinition:
        """
        Step 4: Enrichment.
        Given a structure (Table, Columns), infer:
        - Description
        - Semantic Type (Entity, Text, Num, Time)
        - Data Type (int, float, str)
        - Partition Suitability
        """
        if 'columns' not in schema_dict:
            return self._create_fallback_schema(query)
            
        columns_str = ", ".join([c.get('name', 'Unknown') for c in schema_dict['columns']])
        table_name = schema_dict.get('table_name', 'data')
        
        prompt = SCHEMA_TYPE_INFERENCE_PROMPT.format(query=query, table_name=table_name, columns_str=columns_str)
        
        response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
        type_info = {}
        primary_keys_from_enrichment = []
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            parsed_data = json.loads(content)
            type_info = parsed_data.get("columns", {})
            # Try to extract primary keys from enrichment if provided
            primary_keys_from_enrichment = parsed_data.get("primary_keys", [])
        except Exception as e:
            logger.error(f"Enrichment failed: {e}")

        cols = []
        
        for c_dict in schema_dict['columns']:
            name = c_dict['name']
            info = type_info.get(name, {})
            cols.append(Column(
                name=name,
                description=info.get('description', c_dict.get('description', '')), # Prefer LLM enriched
                semantic_type=info.get('type', 'text'),
                data_type=info.get('data_type', 'text'),
                formatting=info.get('formatting', '')
            ))
        
        # Use primary keys from enrichment if available, otherwise fall back to schema_dict
        # If still empty, infer from column names (common patterns: "id", "name", etc.)
        final_primary_keys = primary_keys_from_enrichment or schema_dict.get('primary_keys', [])
        if not final_primary_keys:
            # Infer primary keys: look for common patterns
            column_names = [c.name.lower() for c in cols]
            for col in cols:
                col_lower = col.name.lower()
                if col_lower in ['id', 'name', 'key'] or col_lower.endswith('_id') or col_lower.endswith('id'):
                    final_primary_keys.append(col.name)
                    break  # Use first match as primary key
            # If still no primary key found, use first column as fallback
            if not final_primary_keys and cols:
                final_primary_keys = [cols[0].name]
            
        return SchemaDefinition(
            table_name=schema_dict.get('table_name', 'extracted_data'),
            columns=cols,
            primary_keys=final_primary_keys
        )

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


        
    def _create_fallback_schema(self, query: str) -> SchemaDefinition:
        return SchemaDefinition(
            table_name="data",
            columns=[Column(name="Content", description="Extracted content")],
            primary_keys=[]
        )
