import logging
import pandas as pd
import json
import asyncio
from typing import List, Dict, Any, Optional
from src.prompts import (
    CHUNK_REFINEMENT_PROMPT,
    ENTITY_RESOLUTION_PROMPT
)

logger = logging.getLogger(__name__)

class Validator:
    """
    Validates and cleans the data extracted from LLM.
    Phase 2 Implementation:
    - Chunk Validation: Per-partition cleaning (constraints, types).
    - Global Validation: Deduplication and consistency checks.
    """
    
    def __init__(self, llm_client=None):
        self.llm = llm_client

    async def validate_chunk(self, df: pd.DataFrame, context_constraints: Dict[str, Any]) -> pd.DataFrame:
        """
        Validates a single chunk of data against constraints.
        If violations found, attempts to REFINE using LLM.
        """
        if df.empty:
            return df
            
        initial_len = len(df)
        
        # 1. Enforce Constraints
        for col, val in context_constraints.items():
            if col in df.columns:
                val_str = str(val).lower()
                
                def check_match(x):
                    x_str = str(x).lower()
                    # 1. Range Check
                    import re
                    range_match = re.match(r'^(\d+)-(\d+)$', val_str.strip())
                    if range_match:
                        try:
                            min_val, max_val = map(int, range_match.groups())
                            x_clean = re.sub(r'[^\d.-]', '', x_str)
                            if x_clean:
                                x_num = float(x_clean)
                                return min_val <= x_num <= max_val
                        except ValueError:
                            pass
                    # 2. String Containment
                    return x_str in val_str or val_str in x_str

                matches = df[col].apply(check_match)
                
                dropped_rows = df[~matches]
                if not dropped_rows.empty and self.llm:
                    logger.info(f"ChunkValidator: {len(dropped_rows)} rows violate {col}={val}. Attempting refinement.")
                    
                    # Refine
                    refined_data = await self.refine_chunk(dropped_rows.to_dict('records'), {col: val})
                    
                    # Re-verify refined data (simple check)
                    refined_df = pd.DataFrame(refined_data)
                    if not refined_df.empty and col in refined_df.columns:
                        # Recursive check? No, just keep valid ones to avoid loop
                        matches_refined = refined_df[col].apply(check_match)
                        valid_refined = refined_df[matches_refined]
                        
                        # Merge back valid rows
                        df = pd.concat([df[matches], valid_refined], ignore_index=True)
                    else:
                        df = df[matches]
                else:
                    df = df[matches]
        
        if len(df) < initial_len:
            logger.info(f"ChunkValidator: Dropped {initial_len - len(df)} rows violating constraints {context_constraints}")
            
        return df

    async def refine_chunk(self, rows: List[Dict], constraints: Dict[str, Any]) -> List[Dict]:
        """
        Asks LLM to fix rows that violate constraints.
        """
        if not rows:
            return []
            
        prompt = CHUNK_REFINEMENT_PROMPT.format(
            constraints=json.dumps(constraints),
            rows=json.dumps(rows, indent=2)
        )
        
        try:
            response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            data = json.loads(content)
            return data.get("fixed_rows", [])
        except Exception as e:
            logger.warning(f"Refinement failed: {e}")
            return []

    async def clean_data(self, df: pd.DataFrame, primary_keys: List[str] = None, context: dict = None) -> pd.DataFrame:
        """
        Global validation and deduplication (Async).
        Includes LLM-based Entity Resolution.
        """
        context = context or {}
        if df.empty:
            return df
            
        initial_len = len(df)
        
        # 1. Structural cleaning
        df = df.dropna(how='all') 
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
        
        # 2. Structural Deduplication (Exact)
        df = df.drop_duplicates()
        
        # 3. LLM Entity Resolution (Semantic Deduplication)
        if self.llm and primary_keys:
             # Identify potential collision columns (Content Columns)
            content_cols = []
            name_candidates = ['Name', 'Entity', 'Title', 'CountryName', 'CityName', 'CommonName', 'ScientificName', 'Symbol', 'PokemonName']
            for col in df.columns:
                if col in name_candidates:
                    content_cols.append(col)
            
            if not content_cols:
                content_cols = [c for c in df.columns if not c.lower().endswith('id')]
            
            # If we have content columns, we check for variants
            if content_cols:
                # Group by these columns might be too strict if there are variants ("USA" vs "U.S.A.")
                # Ideally we cluster. For now, we look for EXACT match on Name to find "Variant Rows" (e.g. diff ID, same Name)
                # Or we can do simple fuzzy blocking if we had a library.
                # Let's stick to the user req: "Duplications and Variants".
                # We'll use the existing logic: Group by Content Cols -> If >1 row, Ask LLM to Merge.
                
                duplicates = df[df.duplicated(subset=content_cols, keep=False)]
                if not duplicates.empty:
                     logger.info(f"GlobalValidator: Found {len(duplicates)} potential semantic duplicates. Resolving...")
                     df = await self.resolve_entities(df, duplicates, content_cols)
        
        # 4. Renumber IDs
        if primary_keys:
            for pk in primary_keys:
                if pk.lower().endswith('id') and pk in df.columns:
                     # Check if it looks numerical roughly
                     df[pk] = range(1, len(df) + 1)
                     logger.info(f"GlobalValidator: Regenerated {pk}.")

        final_len = len(df)
        logger.info(f"GlobalValidator cleaned data: {initial_len} -> {final_len} rows.")
        return df

    async def resolve_entities(self, full_df: pd.DataFrame, duplicates_df: pd.DataFrame, group_cols: List[str]) -> pd.DataFrame:
        """
        Resolves duplicates by asking LLM to merge rows.
        """
        resolved_rows = []
        # Group by the content columns
        grouped = duplicates_df.groupby(group_cols)
        
        row_indices_to_drop = duplicates_df.index
        
        for name, group in grouped:
            rows = group.to_dict('records')
            if len(rows) < 2:
                resolved_rows.extend(rows)
                continue
                
            prompt = ENTITY_RESOLUTION_PROMPT.format(
                rows=json.dumps(rows, indent=2)
            )
            
            try:
                response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
                content = response.content
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                data = json.loads(content)
                merged = data.get("resolved_rows", rows) # Fallback to original
                resolved_rows.extend(merged)
            except Exception as e:
                logger.warning(f"Entity resolution failed for group {name}: {e}")
                resolved_rows.extend(rows)

        # Drop original duplicates from full_df and append resolved
        start_len = len(full_df)
        full_df = full_df.drop(row_indices_to_drop)
        
        if resolved_rows:
            resolved_df = pd.DataFrame(resolved_rows)
            # Align columns
            resolved_df = resolved_df.reindex(columns=full_df.columns)
            full_df = pd.concat([full_df, resolved_df], ignore_index=True)
            
        return full_df
        
    async def verify_consistency_llm(self, df: pd.DataFrame, sample_size: int = 5) -> bool:
        """
        Spot check a few rows using LLM to verify they make sense.
        Returns True if pass, False if suspicious.
        """
        if df.empty or not self.llm:
            return True
            
        # Sample rows
        sample = df.sample(n=min(len(df), sample_size))
        
        # TODO: Implement actual LLM verification call
        # prompt = "Is {row} a valid entity?"
        
        return True
