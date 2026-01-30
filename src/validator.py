import logging
import pandas as pd
from typing import List, Dict, Any, Optional

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

    def validate_chunk(self, df: pd.DataFrame, context_constraints: Dict[str, Any]) -> pd.DataFrame:
        """
        Validates a single chunk of data against constraints.
        e.g. if context_constraints={'State': 'California'}, ensure 'State' column is 'California'.
        """
        if df.empty:
            return df
            
        initial_len = len(df)
        
        # 1. Enforce Constraints
        for col, val in context_constraints.items():
            if col in df.columns:
                # Fuzzy match or exact match? Exact for now.
                # Normalize both
                # df = df[df[col].astype(str).str.lower() == str(val).lower()]
                
                # Soft enforcement: If column exists, filter. 
                # If column value is missing/null, maybe keep or drop? 
                # Let's drop explicit mismatches.
                # Bidirectional containment to handle "Generation 2" vs "2"
                val_str = str(val).lower()
                
                def check_match(x):
                    x_str = str(x).lower()
                    
                    # 1. Range Check (e.g. "1-10", "1900-2000")
                    # Simple regex for positive integers ranges
                    import re
                    range_match = re.match(r'^(\d+)-(\d+)$', val_str.strip())
                    if range_match:
                        try:
                            min_val, max_val = map(int, range_match.groups())
                            # Try to parse x as float/int
                            # Remove non-numeric chars except . and -
                            x_clean = re.sub(r'[^\d.-]', '', x_str)
                            if x_clean:
                                x_num = float(x_clean)
                                return min_val <= x_num <= max_val
                        except ValueError:
                            pass # Not a number, fall through to string check
                    
                    # 2. String Containment (Bidirectional)
                    return x_str in val_str or val_str in x_str

                matches = df[col].apply(check_match)
                
                dropped_rows = df[~matches]
                if not dropped_rows.empty:
                    logger.warning(f"ChunkValidator: Dropped {len(dropped_rows)} rows violating constraint {col}={val}. Example Values: {dropped_rows[col].head(3).tolist()}")
                
                df = df[matches]
        
        if len(df) < initial_len:
            logger.info(f"ChunkValidator: Dropped {initial_len - len(df)} rows violating constraints {context_constraints}")
            
        return df

    def clean_data(self, df: pd.DataFrame, primary_keys: List[str] = None) -> pd.DataFrame:
        """
        Global validation and deduplication.
        """
        if df.empty:
            return df
            
        initial_len = len(df)
        
        # 1. Structural cleaning
        df = df.dropna(how='all') 
        
        # 2. String normalization
        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
        
        # 3. Deduplication
        if primary_keys:
            # Case insensitive check for keys?
            # For now, standard pandas drop_duplicates
            missing_pks = [k for k in primary_keys if k not in df.columns]
        df_clean = df # Initialize df_clean
        if not primary_keys:
            # Fallback to all columns if no PKs
            dedup_cols = list(df.columns)
            df_clean = df.drop_duplicates()
        else:
            # V2: Constraint-Aware Deduplication
            # LOGIC FIX: LLM-generated integer IDs (e.g. 1, 2, 3) collide across partitions.
            # We must DEDUPLICATE based on CONTENT (Names), not ID.
            
            # 1. Identify Content Columns
            content_cols = []
            # Add known "Name" variations
            name_candidates = ['Name', 'Entity', 'Title', 'CountryName', 'CityName', 'CommonName', 'ScientificName', 'Symbol', 'PokemonName']
            for col in df.columns:
                if col in name_candidates:
                    content_cols.append(col)
            
            # If no specific name column found, use all non-ID columns
            if not content_cols:
                content_cols = [c for c in df.columns if not c.lower().endswith('id')]
            
            # If still empty (only IDs?), use everything
            if not content_cols:
                content_cols = list(df.columns)
                
            dedup_cols = content_cols
            
            # 2. Log Duplicates (User Request)
            duplicates = df[df.duplicated(subset=dedup_cols, keep=False)]
            if not duplicates.empty:
                # Group by dedup cols to see the groups
                logger.info(f"GlobalValidator: Identified {len(duplicates)} rows involved in duplication/variants.")
                # Log first 5 groups
                grouped = duplicates.groupby(dedup_cols)
                for name, group in list(grouped)[:5]:
                    logger.info(f"  Duplicate Group ({name}):")
                    for _, row in group.iterrows():
                        logger.info(f"    - {row.to_dict()}")
            
            # 3. Drop Duplicates
            df_clean = df.drop_duplicates(subset=dedup_cols)
            
            # 4. Renumber IDs (Fix Collision)
            # If an ID column exists in primary_keys, regenerate it 1..N
            for pk in primary_keys:
                if pk.lower().endswith('id'):
                    # Check if it looks like an integer ID (not distinct UUIDs)
                    # We Just Regenerate to be safe and consistent
                    df_clean[pk] = range(1, len(df_clean) + 1)
                    logger.info(f"GlobalValidator: Regenerated {pk} from 1 to {len(df_clean)} to resolve collisions.")
            
        logger.info(f"GlobalValidator: Dropped {len(df) - len(df_clean)} duplicates.")
        logger.info(f"GlobalValidator cleaned data: {initial_len} -> {len(df_clean)} rows. Columns: {list(df_clean.columns)}")
        return df_clean
        
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
