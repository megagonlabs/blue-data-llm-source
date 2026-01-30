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
    partition_strategy: Optional[List[str]] = None  # Architecture V2: Explicit partition subset
    
    def to_dict(self):
        return {
            "table_name": self.table_name,
            "columns": [c.to_dict() for c in self.columns],
            "primary_keys": self.primary_keys
        }
        
    def get_partition_keys(self) -> List[Column]:
        if self.partition_strategy:
             # V2: Use explicit strategy
             return [c for c in self.columns if c.name in self.partition_strategy]
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
        
    async def infer_schema(self, query: str, num_candidates: Optional[int] = None, strategy: str = "llm_rank") -> SchemaDefinition:
        """
        Main entry point: given a query like "List all US cities",
        infer the best schema.
        Phase 2 Update:
        1. Generate Global Schema Pool (Hidden/Candidate Columns)
        2. Generate Schema Candidates (selecting from pool)
        3. Rank & Refine
        """
        # Step 1: Generate Global Schema Pool of latent attributes
        global_pool = await self._generate_candidate_columns(query)
        logger.info(f"Generated global schema pool: {global_pool}")
        
        # Step 2: Generate Schema Candidates using the pool
        k = num_candidates or self.default_num_candidates
        candidates = await self._generate_candidates(query, k, global_pool)
        
        if not candidates:
            # Fallback
            return self._create_fallback_schema(query)
            
        if strategy == "first":
            best_candidate = candidates[0]
        else:
            best_candidate = await self._rank_candidates(query, candidates)
        
        # Step 3: Refine types and keys
        final_schema = await self._infer_types_and_keys(query, best_candidate)
        return final_schema

    async def _generate_candidate_columns(self, query: str) -> List[str]:
        """
        Phase 2.1: Generate a pool of ~15 potential attributes.
        Include 'hidden' attributes good for partitioning (Time, Location, Category).
        """
        prompt = f"""
        User Query: "{query}"
        
        Brainstorm 15 potential attributes (columns) for a database table satisfying this query.
        Include:
        - Visible attributes (directly requested)
        - Latent/Hidden attributes (e.g. "Release Year", "Country", "Genre", "Scientific Class", "Generation") that are valuable for filtering or partitioning.
        
        Return JSON list of strings.
        Example: ["Name", "year", "genre", "director", "studio"]
        """
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

    async def _generate_candidates(self, query: str, k: int, column_pool: List[str] = []) -> List[Dict]:
        pool_str = ", ".join(column_pool)
        prompt = f"""
        You are an expert database architect that designs database schemas to decompose user query by partitions.
        User Query: "{query}"
        Available Attribute Pool: [{pool_str}]
        
        Propose {k} different table schemas that split user query into subqueries (partitions).
        Select the best subset of attributes from the pool (and add unique IDs if needed).
        For each schema, provide:
        1. Table Name
        2. List of Columns (Name, Description)
        3. Primary Key(s)
        4. Partition Strategy: A list of columns to use for query decomposition, ordered by hierarchy (e.g. ["Year", "Month"]).
        
        Guidelines:
        1. **Entity Centric**: The schema MUST include the main Entity Name (e.g. "City", "Pokemon Name").
        2. **Decomposition Ready**: Choose attributes that make good Partition Keys (e.g. "Region", "Type", "Year").
        3. **Scope Aware**: Respect constraints (e.g. if "US Cities", "Country" is a static attribute/constraint, maybe less useful for partitioning).
        4. **Complete**: Ensure all requested data can be stored.
        
        CRITICAL: 
        - Do NOT produce schemas with ONLY IDs.
        - Ensure clear, descriptive column names.
        
        Output format: JSON object with key "schemas".
        
        Output Template (JSON):
        {{
            "schemas": [
                {{
                    "table_name": "EntityTableName",
                    "columns": [
                        {{"name": "Attribute1", "description": "Description of attribute"}},
                        {{"name": "Attribute2", "description": "Description of another attribute"}}
                    ],
                    "primary_keys": ["Attribute1"],
                    "partition_strategy": ["Attribute1", "Attribute2"]  # Architecture V2: List distinct keys in order of hierarchy
                }}
            ]
        }}
        """
        
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
        prompt = f"""
        User Query: "{query}"
        
        I have {len(candidates)} candidate schemas:
        {candidates_str}
        
        Which schema is the BEST for general-purpose extraction if I want to decompose the query into subqueries (partitions)?
        Rank based on:
        - the partitions under this schema are independent and can be processed in parallel
        - the partitions under this schema are roughly equal in size
        - Atomicity (columns are not composite)
        - Partition Strategy Viability: Does the proposed partition strategy (if any) make sense for the query?
        - Orthogonality (no redundant columns)
        - Coverage (captures all necessary attributes in the query)
        
        BAD Schemas:
        - Only Numeric stats (no Names)
        - Only IDs and Foreign Keys
        - Missing crucial grouping columns
        
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
        if 'columns' not in schema_dict:
            logger.error(f"Schema dict missing 'columns': {schema_dict}")
            return self._create_fallback_schema(query)
            
        columns_str = ", ".join([c.get('name', 'Unknown') for c in schema_dict['columns']])
        
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
            primary_keys=schema_dict.get('primary_keys', []),
            partition_strategy=schema_dict.get('partition_strategy', [])
        )
        
    def _create_fallback_schema(self, query: str) -> SchemaDefinition:
        return SchemaDefinition(
            table_name="data",
            columns=[Column(name="Content", description="Extracted content", is_partition_key=False)]
        )
