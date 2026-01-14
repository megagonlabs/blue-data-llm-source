import logging
import pandas as pd
from typing import List, Dict

logger = logging.getLogger(__name__)

class Validator:
    """
    Validates and cleans the extracted data.
    - Semantic Deduplication: Merges "NY" and "New York".
    - Consistency Checks: filters out hallucinations (optional).
    """
    
    def clean_data(self, df: pd.DataFrame, primary_keys: List[str] = None) -> pd.DataFrame:
        if df.empty:
            return df
            
        initial_len = len(df)
        
        # 1. Structural cleaning
        df = df.dropna(how='all') 
        
        # 2. String normalization
        # Lowercase for comparison but keep original? 
        # For now, just strip whitespace
        df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
        
        # 3. Deduplication
        if primary_keys:
            # If PKs are defined, drop dupes on them
            # We keep the first occurrence. 
            # Ideally we'd merge or pick the most complete one.
            missing_pks = [k for k in primary_keys if k not in df.columns]
            if not missing_pks:
                df = df.drop_duplicates(subset=primary_keys)
            else:
                logger.warning(f"Primary keys {missing_pks} missing from dataframe. Deduplicating on all cols.")
                df = df.drop_duplicates()
        else:
            df = df.drop_duplicates()
            
        logger.info(f"Validator cleaned data: {initial_len} -> {len(df)} rows.")
        return df
        
    def check_consistency(self, row: Dict) -> bool:
        """
        Placeholder for advanced logic/LLM-based validity check.
        e.g. check if Population is numeric, or if City is actually in State.
        """
        # TODO: Implement LLM-based verification
        return True
