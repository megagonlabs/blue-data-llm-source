import json
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
import hashlib

from utils.llm_utils import LLMClient

logger = logging.getLogger(__name__)

@dataclass
class Column:
    name: str
    description: str = ""
    semantic_type: str = "text" # text, categorical, numerical, time, entity
    is_partition_key: bool = False
    
    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "semantic_type": self.semantic_type,
            "is_partition_key": self.is_partition_key
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
    
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        
    async def infer_schema(self, query: str, num_candidates: int = 3) -> SchemaDefinition:
        """
        Main entry point: given a query like "List all US cities",
        infer the best schema (e.g. Columns: City, State, Population).
        """
        candidates = await self._generate_candidates(query, num_candidates)
        if not candidates:
            logger.warning("No schema candidates generated. Fallback to simple schema.")
            return self._create_fallback_schema(query)
            
        best_candidate = await self._rank_candidates(query, candidates)
        
        # Enrich with types if not already present or needs refinement
        final_schema = await self._infer_types_and_keys(query, best_candidate)
        return final_schema

    async def _generate_candidates(self, query: str, k: int) -> List[Dict]:
        prompt = f"""
        You are an expert database architect.
        User Query: "{query}"
        
        Propose {k} different table schemas that could store the data requested by this query.
        For each schema, provide:
        1. Table Name
        2. List of Columns (Name, Description)
        3. Primary Key(s)
        
        CRITICAL: The schema MUST include the main Entity Name (e.g., "Title" for movies, "Name" for people, "City" for cities). 
        Do NOT produce schemas with only IDs. 
        Ensure columns are atomic and comprehensive.
        
        Output format: JSON object with key "schemas" containing a list of schema objects.
        Example:
        {{
            "schemas": [
                {{
                    "table_name": "us_cities",
                    "columns": [
                        {{"name": "City", "description": "Name of the city"}},
                        {{"name": "State", "description": "State containing the city"}}
                    ],
                    "primary_keys": ["City", "State"]
                }}
            ]
        }}
        """
        
        response = await self.llm.agenerate(prompt, temperature=0.7, response_format={"type": "json_object"})
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            candidates = json.loads(content)
            if isinstance(candidates, dict) and "schemas" in candidates:
                return candidates["schemas"]
            return candidates if isinstance(candidates, list) else []
        except Exception as e:
            logger.error(f"Failed to parse schema candidates: {e}")
            return []

    async def _rank_candidates(self, query: str, candidates: List[Dict]) -> Dict:
        if len(candidates) == 1:
            return candidates[0]
            
        # LLM-as-a-Judge ranking
        candidates_str = json.dumps(candidates, indent=2)
        prompt = f"""
        User Query: "{query}"
        
        I have {len(candidates)} candidate schemas:
        {candidates_str}
        
        Which schema is the BEST for exhaustive retrieval and data cleanliness?
        Consider:
        - Atomicity (columns are not composite)
        - Orthogonality (no redundant columns)
        - Coverage (captures all necessary attributes)
        
        Return ONLY the index (0-based) of the best schema.
        """
        response = await self.llm.agenerate(prompt, temperature=0.0)
        try:
            # Extract first number
            idx = int("".join(filter(str.isdigit, response.content.strip()))[0])
            if 0 <= idx < len(candidates):
                return candidates[idx]
        except:
            logger.warning("Ranking parsing failed, selecting first candidate.")
            
        return candidates[0]

    async def _infer_types_and_keys(self, query: str, schema_dict: Dict) -> SchemaDefinition:
        # Determine semantic types and partition suitability
        columns_str = ", ".join([c['name'] for c in schema_dict['columns']])
        
        prompt = f"""
        Query: "{query}"
        Table: {schema_dict.get('table_name', 'data')}
        Columns: {columns_str}
        
        For each column, determine:
        1. Semantic Type (categorical, numerical, time, entity, text)
        2. Is it a good Partition Key? 
           - Good keys divide data into chunks (e.g. State, Year, Genre, Generation, Phase).
           - BAD keys: Unique IDs (e.g. 1, 2, 3...), High cardinality names (City Name), Booleans (too few).
           
        Return JSON:
        {{
            "columns": {{
                "ColumnName": {{"type": "...", "is_partition": true/false}},
                ...
            }}
        }}
        """
        
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
