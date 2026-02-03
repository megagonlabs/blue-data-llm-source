import logging
import json
import traceback
from typing import List, Dict, Tuple, Optional
from utils.llm_utils import LLMClient

logger = logging.getLogger(__name__)

GEN_ASSERTION_PROMPT = """
You are a QA Engineer interacting with a Python Data Validator.
Your goal is to write a set of Python `assert` statements to validate a row of data.

Context:
Schema/Description: {schema_desc}
Sample Row: {sample_row}

Instructions:
1. Write 3-5 critical assertions that must hold true for this data.
2. Focus on numerical ranges, data types, date formats, and non-empty fields.
3. Assume the variable is named `row`.
4. Do NOT use external libraries except `re` (regular expressions) or standard `datetime`.
5. **IMPORTANT**: Do NOT invent strict mathematical formulas between columns (e.g. `Total = A + B`) unless the relationship is completely obvious and universal. Real-world data often has noise. Prefer looser bounds (e.g. `Total >= A`, `Total >= B`).
6. Return ONLY the python code block.

Example:
assert row['Population'] > 0, "Population must be positive"
assert '20' in row['Date'], "Date must be 20th century or later"

Code:
"""

FIX_DATA_PROMPT = """
You are a Data Repair Agent.
We found errors in the following data rows based on python assertions.

Error Report:
{error_report}

Instructions:
1. Fix the values in the rows to satisfy the assertions. 
2. If a value is unfixable (e.g. clearly garbage), set it to None or empty string.
3. Return corrected rows as a JSON list.
"""

class CodeValidator:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def generate_assertions(self, schema_desc: str, sample_row: Dict) -> str:
        """Retrieves python assertion code from LLM."""
        prompt = GEN_ASSERTION_PROMPT.format(
            schema_desc=schema_desc,
            sample_row=json.dumps(sample_row, default=str)[:500]
        )
        response = await self.llm.agenerate(prompt, temperature=0.0)
        code = response.content
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0]
        elif "```" in code:
            code = code.split("```")[1].split("```")[0]
        return code

    def execute_validation(self, data: List[Dict], assertion_code: str) -> Tuple[List[Dict], List[Dict]]:
        """
        Executes assertions on data.
        Returns (valid_rows, invalid_rows_with_errors).
        """
        valid_rows = []
        invalid_rows = []
        
        # Pre-compile check
        try:
            compile(assertion_code, '<string>', 'exec')
        except Exception as e:
            logger.error(f"Failed to compile assertion code: {e}")
            return data, [] # Fail open (assume valid if code is bad)

        import re
        import datetime
        
        for i, row in enumerate(data):
            try:
                # Safe-ish exec environment
                local_scope = {'row': row, 're': re, 'datetime': datetime}
                exec(assertion_code, {}, local_scope)
                valid_rows.append(row)
            except AssertionError as e:
                err_msg = str(e) or "Assertion Failed"
                invalid_rows.append({"row_index": i, "original": row, "error": err_msg})
            except Exception as e:
                # Runtime error in checking (e.g. key error)
                invalid_rows.append({"row_index": i, "original": row, "error": f"Runtime Check Error: {e}"})
                
        return valid_rows, invalid_rows

    async def fix_data(self, invalid_rows: List[Dict]) -> List[Dict]:
        """Asks LLM to fix invalid rows."""
        if not invalid_rows:
            return []
            
        # Batch fix if too many? For now take top 10
        target_errors = invalid_rows[:10]
        report = []
        for item in target_errors:
            report.append(f"Row {item['row_index']}: {item['original']} => Error: {item['error']}")
            
        prompt = FIX_DATA_PROMPT.format(error_report="\n".join(report))
        response = await self.llm.agenerate(prompt, temperature=0.0, response_format={"type": "json_object"})
        
        fixed_data = []
        try:
            content = response.content
            # Unwrap
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            parsed = json.loads(content)
            
            # Helper to find list
            result_list = []
            if isinstance(parsed, list):
                result_list = parsed
            elif isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(v, list):
                        result_list = v
                        break
            
            fixed_data = result_list
            logger.info(f"CodeValidator: Fixed {len(fixed_data)} rows.")
            
        except Exception as e:
            logger.error(f"CodeValidator Fix Failed: {e}")
            
        return fixed_data

    async def run(self, data: List[Dict], schema_desc: str) -> List[Dict]:
        """Main entry point."""
        if not data:
            return []
            
        # 1. Generate Assertions based on first row
        try:
            assertions = await self.generate_assertions(schema_desc, data[0])
            logger.info(f"Generated Assertions:\n{assertions}")
        except Exception as e:
            logger.error(f"Assertion Generation Failed: {e}")
            return data

        # 2. Validate
        try:
            valid, invalid = self.execute_validation(data, assertions)
        except Exception as e:
            logger.error(f"Validation Execution Failed: {e}")
            return data
        
        if not invalid:
            return valid
            
        logger.info(f"CodeValidator: {len(invalid)}/{len(data)} rows failed assertions. Attempting fix...")
        
        # 3. Fix
        fixed = []
        try:
            fixed = await self.fix_data(invalid)
        except Exception as e:
            logger.error(f"Fix Failed: {e}")
        
        # 4. Merge (Soft Validator: Keep even unfixed rows to preserve Recall)
        # Identify rows that were successfully fixed (by index or content?)
        # For simplicity in this v1, we assume 'fixed' list replaces 'invalid' list 1-to-1 if length matches,
        # otherwise we might be duplicating or losing.
        # But fix_data returns a new list. 
        # Strategy: Valid + Fixed + Remaining Invalid (that weren't fixed)
        
        # If fix coverage is partial, we should just append fixed, and then what about the rest?
        # Current implementation of fix_data returns ONLY corrected rows. 
        # We will assume: Valid + Fixed + (Invalid - Fixed_Indices).
        # Actually, let's just append Fixed and any Invalid rows that WEREN'T fixed.
        # But fix_data doesn't return indices.
        # Simplified Fail-Safe: Valid + Fixed + Invalid (if fix failed totally).
        # Better: Valid + Fixed + [Unfixed Invalid].
        # For now, simply: final_data = valid + fixed + (invalid[len(fixed):] if len(fixed) < len(invalid) else [])
        
        # To be safe and maximize recall:
        if len(fixed) < len(invalid):
            logger.warning(f"CodeValidator: Only fixed {len(fixed)}/{len(invalid)} rows. Keeping the rest as-is.")
            # Reconstruct original invalid rows that were not fixed
            # (This is imprecise without ID tracking, but acceptable for now)
            unfixed_count = len(invalid) - len(fixed)
            unfixed_originals = [item['original'] for item in invalid[len(fixed):]]
            final_data = valid + fixed + unfixed_originals
        else:
            final_data = valid + fixed

        return final_data
