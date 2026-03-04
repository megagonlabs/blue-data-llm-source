from thefuzz import process, fuzz
import numpy as np
import os
import sys
import io
import json
import pandas as pd
import numpy as np
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.llm_utils import LLMClient
import litellm
import traceback

litellm.drop_params = True

OUTPUT_DIR = "md_antigravity/trial_tables"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_table_from_url(url, table_idx=-1, min_rows=10):
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        
        # Insert spaces around structural tags to prevent pandas read_html from smashing text nodes together
        import re
        html_text = re.sub(r'(?i)</?(?:br|li|div|p)\b[^>]*>', ' ', response.text)
        
        html_content = io.StringIO(html_text)
        dfs = pd.read_html(html_content)
        
        if isinstance(table_idx, list):
            valid_dfs = [dfs[i] for i in table_idx if i < len(dfs)]
            if valid_dfs:
                return pd.concat(valid_dfs, ignore_index=True)
            return None
        elif table_idx != -1 and table_idx < len(dfs):
            return dfs[table_idx]
            
        for df in dfs:
            if df.shape[0] >= min_rows and df.shape[1] >= 2:
                return df
        return None
    except Exception as e:
        print(f"Error fetching table: {e}")
        return None

def clean_df(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ['_'.join(str(c) for c in col).strip() for col in df.columns]
        
    # clean headers
    df.columns = df.columns.astype(str)
    df.columns = df.columns.str.replace(r'\[.*?\]', '', regex=True)
    df.columns = df.columns.str.replace(r'[\*†‡]', '', regex=True)
    df.columns = df.columns.str.strip()
    
    # drop reference columns
    cols_to_drop = [c for c in df.columns if 'Ref' in str(c) or 'Notes' in str(c)]
    df = df.drop(columns=cols_to_drop)
    
    # remove footnote references in string cells
    df = df.astype(str)
    for col in df.columns:
        df[col] = df[col].str.replace(r'\[.*?\]', '', regex=True)
        # trim asterisk and dagger/double-dagger often found in wikipedia tables
        df[col] = df[col].str.replace(r'[\*†‡]', '', regex=True)
        # trim whitespaces
        df[col] = df[col].str.strip()
        
    # Replace N/A representations with NaN (case-insensitive regex)
    df = df.replace(r'(?i)^\s*(-?N/?A|-|—|nan?)\s*$', np.nan, regex=True)
    return df

def fuzzy_merge(df1, df2, key1, key2, threshold=85, limit=1):
    """
    Fuzzy merge two dataframes on specific keys.
    """
    s_df2 = df2[key2].dropna().tolist()
    
    matched_indices = []
    
    for val in df1[key1]:
        if pd.isna(val):
            matched_indices.append(None)
            continue
            
        # get best match
        matches = process.extract(val, s_df2, limit=limit, scorer=fuzz.token_sort_ratio)
        if matches:
            best_match, score = matches[0][0], matches[0][1]
            if score >= threshold:
                matched_indices.append(best_match)
            else:
                matched_indices.append(None)
        else:
            matched_indices.append(None)
            
    # create a temporary matching column
    df1['__match_key'] = matched_indices
    merged = pd.merge(df1, df2, left_on='__match_key', right_on=key2, suffixes=('_1', '_2'), how='inner')
    merged = merged.drop(columns=['__match_key'])
    return merged

def refine_with_llm(df, current_query, task_type, primary_keys, user_feedback=""):
    columns = list(df.columns)
    
    feedback_section = ""
    if user_feedback:
        feedback_section = f"\nCRITICAL USER FEEDBACK FOR THIS INSTANCE:\n{user_feedback}\nYou MUST completely adhere to these instructions when renaming columns, dropping columns, refining the query, and dropping rows.\n"

    prompt = f"""You are a data curation assistant preparing a high-quality table QA dataset limit.
I have a combined table created by a {task_type} operation.
The current columns are: {columns}
The logical primary keys are: {primary_keys}
The current query is: "{current_query}"{feedback_section}

Please do the following:
1. Rename the columns to be semantically meaningful, concise, and clean. 
   - Remove suffixes like _1, _2. 
   - Avoid underscores to combine entity and unit; instead use parentheses for units, e.g., "Total Area (sq mi)".
   - Remove "(est.)", "bonus", and other strange artifacts.
   - Replace unicode characters with plain text (e.g. arrows -> "to").
   - If there are duplicate column semantics, distinguish them clearly (e.g., "Population (2020)", "Population (2025)").
   - For Life Expectancy or Median Age, simplify names to be intuitive, e.g. "Male Life Expectancy (80 yrs)" or similar. 
   - IF A COLUMN is redundant (e.g. repeated join keys like 'Country_2' when 'Country_1' is present), map it to "DROP".
   - IF A COLUMN is mostly just Wikipedia index tracking or non-semantic history/year columns, map it to "DROP".
   - IF A COLUMN has a trailing [A] or [a], clean it up!
   - IF THE USER FEEDBACK asks to drop a column, make sure its new name is EXACTLY "DROP".
2. Refine the query to be natural, unambiguous, and clear. Avoid confusing phrasings like "from 2020 without end". Use "in 2022 and 2023" instead of "from 2022 and 2023". Use specific years if available.
3. Because you are renaming columns, you MUST return the updated mapping for the primary keys so that we don't lose track of them.

Return a JSON document with EXACTLY four keys:
"new_columns": A list of strings of the exact same length as the input columns list, containing the new column names. For columns to drop, output "DROP" as its name.
"new_primary_keys": A list of strings representing the new names of the primary keys after your renaming step.
"new_query": The refined natural language query.
"rows_to_drop": A list of integer indices for any specific rows the user asked to drop (extract these from the Dataframe Preview). Empty list if none.
"""
    try:
        from utils.llm_utils import LLMClient
        client = LLMClient(model="gpt-5", default_use_cache=True)
        
        # pass the stringified dataframe to the LLM so it can actually find row indices to drop
        df_head_context = "Dataframe Preview (first 5 rows):\\n" + df.head(5).to_string()
        df_tail_context = "Dataframe Preview (last 5 rows):\\n" + df.tail(5).to_string()
        
        full_prompt = prompt + "\\n\\n" + df_head_context + "\\n\\n" + df_tail_context
        
        response = client.generate(
            full_prompt, 
            response_format={"type": "json_object"}
        )
        res = json.loads(response.content)
        return res.get("new_columns", columns), res.get("new_query", current_query), res.get("new_primary_keys", primary_keys), res.get("rows_to_drop", [])
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"LLM failed: {e}")
        return columns, current_query, []

def detect_aggregate_keys_with_llm(keys_list):
    prompt = f"""You are a data curation assistant preparing a high-quality table QA dataset.
Here is a list of primary keys from a dataset:
{keys_list}

Identify which of these keys represent an aggregate, summary, grouping, or total row rather than a single entity instance. 
Examples of things to catch: 'World', 'Total US and territories', 'The 50 states', 'All countries', 'Global', 'European Union', 'Total', 'Average', 'Median', etc.
Only include items that are definitively totals or aggregates. Do not include random regular names.

Return a JSON document with EXACTLY one key:
"aggregate_keys": A list of exact string matches from the provided list that are aggregates/totals. If none, return an empty list [].
"""
    try:
        from utils.llm_utils import LLMClient
        client = LLMClient(model="gpt-5", default_use_cache=True)
        response = client.generate(
            prompt, 
            response_format={"type": "json_object"}
        )
        res = json.loads(response.content)
        return res.get("aggregate_keys", [])
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"LLM aggregate detection failed: {e}")
        return []

dataset_cfg = []

tasks = [
    {
        "id": "trial_join_1",
        "type": "JOIN",
        "query": "List all countries with their GDP from the IMF and their population from the UN.",
        "primary_key": ["Country"],
        "feedback": "1. Query should be: 'List all countries with their GDP from the IMF for 2026 and their population from the United Nations in 2023.'\\n2. You MUST DROP the following columns: 'World Bank GDP (2024)', 'United Nations GDP (2024)', 'Population (1 July 2022)', 'Population Change (%)'",
        "sources": [
            {"url": "https://en.wikipedia.org/wiki/List_of_countries_by_GDP_(nominal)", "key": "Country/Territory", "table_idx": 2},
            {"url": "https://en.wikipedia.org/wiki/List_of_countries_by_population_(United_Nations)", "key": "Country or territory", "table_idx": 0}
        ],
        "join_how": "inner"
    },
    {
        "id": "trial_join_2",
        "type": "JOIN",
        "query": "List the NOC countries with their medal counts (gold, silver, bronze, total) and rank for the 2020 and 2024 Summer Olympics.",
        "primary_key": ["NOC"],
        "feedback": "",
        "sources": [
            {"url": "https://en.wikipedia.org/wiki/2024_Summer_Olympics_medal_table", "key": "NOC", "table_idx": 2},
            {"url": "https://en.wikipedia.org/wiki/2020_Summer_Olympics_medal_table", "key": "NOC", "table_idx": 2}
        ],
        "join_how": "inner"
    },
    {
        "id": "trial_join_3",
        "type": "JOIN",
        "query": "List the US states with their total area, census population, and House seats in 2020",
        "primary_key": ["State or territory"],
        "feedback": "Keep columns: 'State or Territory', 'Total Area (sq mi)', 'Census population (2020)', 'House seats (2020)'",
        "sources": [
            {"url": "https://en.wikipedia.org/wiki/List_of_U.S._states_and_territories_by_area", "key": "State / territory_State / territory", "table_idx": 0},
            {"url": "https://en.wikipedia.org/wiki/List_of_U.S._states_and_territories_by_population", "key": "State or territory_State or territory", "table_idx": 0}
        ],
        "join_how": "inner"
    },
    {
        "id": "trial_join_4",
        "type": "JOIN",
        "query": "List the films that both belong to the top 50 highest-grossing films and the top 50 most expensive films with their production budgets.",
        "primary_key": ["Film Title"],
        "feedback": "Keep columns: 'Film Title', 'Rank (Highest-Grossing)', 'Worldwide gross (USD)', 'Rank (Most Expensive)', 'Production Budget (USD millions)'",
        "sources": [
            {"url": "https://en.wikipedia.org/wiki/List_of_highest-grossing_films", "key": "Title", "table_idx": 0},
            {"url": "https://en.wikipedia.org/wiki/List_of_most_expensive_films", "key": "Title", "table_idx": 0}
        ],
        "join_how": "inner"
    },
    {
        "id": "trial_join_5",
        "type": "JOIN",
        "query": "List 2024 life expectancy (All, Male, Female) and median age according to CIA World Factbook (2024) for all the countries",
        "primary_key": ["Country"],
        "feedback": "Keep columns: 'Country', 'Life Expectancy (All 2024)', 'Life Expectancy (Male 2024)', 'Life Expectancy (Female 2024)', 'Median Age (2024)'. Drop rank and region columns if any.",
        "sources": [
            {"url": "https://en.wikipedia.org/wiki/List_of_countries_by_life_expectancy", "key": "Countries_Countries", "table_idx": 4},
            {"url": "https://en.wikipedia.org/wiki/List_of_countries_by_median_age", "key": "Country/Territory_Country/Territory_Country/Territory", "table_idx": 0}
        ],
        "join_how": "inner"
    },
    {
        "id": "trial_union_1",
        "type": "UNION",
        "query": "List all the Academy Award winners for Best Actor and Best Actress from 1927/28 (1st) to 2024 (97th)",
        "primary_key": ["Year", "Category"],
        "feedback": "Keep columns: 'Year', 'Category','Winner Name', 'Film Name'. Rename 'Year' to 'Academy Awards Edition'. Drop any empty or unneeded columns.",
        "sources": [
            {"url": "https://en.wikipedia.org/wiki/Academy_Award_for_Best_Actor", "table_idx": [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "filter_pattern": r"[‡§†]", "rename_col": {"Actor": "Winner Name"}, "add_col": {"Category": "Best Actor"}},
            {"url": "https://en.wikipedia.org/wiki/Academy_Award_for_Best_Actress", "table_idx": [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "filter_pattern": r"[‡§†]", "rename_col": {"Actress": "Winner Name"}, "add_col": {"Category": "Best Actress"}}
        ]
    },
    {
        "id": "trial_union_2",
        "type": "UNION",
        "query": "List the Billboard Year-End Hot 100 singles from 2022 to 2025.",
        "primary_key": ["Year", "No."],
        "sources": [
            {"url": "https://en.wikipedia.org/wiki/Billboard_Year-End_Hot_100_singles_of_2025", "table_idx": 0, "add_col": {"Year": "2025"}},
            {"url": "https://en.wikipedia.org/wiki/Billboard_Year-End_Hot_100_singles_of_2024", "table_idx": 0, "add_col": {"Year": "2024"}},
            {"url": "https://en.wikipedia.org/wiki/Billboard_Year-End_Hot_100_singles_of_2023", "table_idx": 0, "add_col": {"Year": "2023"}},
            {"url": "https://en.wikipedia.org/wiki/Billboard_Year-End_Hot_100_singles_of_2022", "table_idx": 0, "add_col": {"Year": "2022"}}
        ]
    },
    {
        "id": "trial_union_3",
        "type": "UNION",
        "query": "List the Nobel Laureates in Physics, Chemistry and Physiology or Medicine from 1945 to 2025.",
        "primary_key": ["Year", "Category", "Laureate"],
        "feedback": "Keep columns: 'Year', 'Category', 'Laureate', 'Nationality'. Drop any empty or unneeded columns, explicitly DROP 'Image' and 'Rationale'.",
        "sources": [
            {"url": "https://en.wikipedia.org/wiki/List_of_Nobel_laureates_in_Physics", "table_idx": 0, "add_col": {"Category": "Physics"}, "drop_pattern": "Not awarded", "year_range": [1945, 2025]},
            {"url": "https://en.wikipedia.org/wiki/List_of_Nobel_laureates_in_Chemistry", "table_idx": 0, "add_col": {"Category": "Chemistry"}, "drop_pattern": "Not awarded", "year_range": [1945, 2025]},
            {"url": "https://en.wikipedia.org/wiki/List_of_Nobel_laureates_in_Physiology_or_Medicine", "table_idx": 0, "add_col": {"Category": "Physiology or Medicine"}, "drop_pattern": "Not awarded", "year_range": [1945, 2025]}
        ]
    },
    {
        "id": "trial_union_4",
        "type": "UNION",
        "query": "List the tallest buildings (skyscrapers) in New York City, Chicago, and Los Angeles that stand at least 650 feet tall based on standard height measurements.",
        "primary_key": ["City", "Name"],
        "feedback": "Keep columns: 'City', 'Name', 'Height (ft)', 'Floors', 'Year'. Explicitly DROP 'Rank', 'Image', 'Location', 'Purpose', 'Notes'. Rename 'Floors[E]' to 'Floors'.",
        "sources": [
            {"url": "https://en.wikipedia.org/wiki/List_of_tallest_buildings_in_New_York_City", "table_idx": 1, "add_col": {"City": "New York City"}, "rename_col": {"Floors[E]": "Floors", "Height ft (m)": "Height (ft)"}, "numeric_strip_paren": ["Height (ft)"], "min_val_filter": {"col": "Height (ft)", "min": 650}},
            {"url": "https://en.wikipedia.org/wiki/List_of_tallest_buildings_in_Chicago", "table_idx": 1, "add_col": {"City": "Chicago"}, "rename_col": {"Height ft (m)": "Height (ft)"}, "numeric_strip_paren": ["Height (ft)"], "min_val_filter": {"col": "Height (ft)", "min": 650}},
            {"url": "https://en.wikipedia.org/wiki/List_of_tallest_buildings_in_Los_Angeles", "table_idx": 1, "add_col": {"City": "Los Angeles"}, "rename_col": {"Height ft (m)": "Height (ft)"}, "numeric_strip_paren": ["Height (ft)"], "min_val_filter": {"col": "Height (ft)", "min": 650}}
        ]
    },
    {
        "id": "trial_union_5",
        "type": "UNION",
        "query": "List video games that sold at least one million copies on a specific platform among PlayStation 4, Nintendo Switch, and Xbox One. Games released on multiple platforms should appear separately for each platform.",
        "primary_key": ["Console", "Game"],
        "feedback": "Keep columns: 'Console', 'Game', 'Copies sold (million)', 'Release date', 'Developer', 'Publisher'. Rename 'Title' to 'Game', 'Release date[a]' to 'Release date', 'Genre(s)' to 'Genre', 'Developer(s)' to 'Developer', 'Publisher(s)' to 'Publisher'. DROP 'As of'.",
        "sources": [
            {"url": "https://en.wikipedia.org/wiki/List_of_best-selling_PlayStation_4_video_games", "table_idx": 1, "add_col": {"Console": "PlayStation 4"}, "rename_col": {"Copies sold": "Copies sold (million)"}, "replace_regex": {"Copies sold (million)": [r"(?i)\s*million", ""]}, "format_date": ["Release date"]},
            {"url": "https://en.wikipedia.org/wiki/List_of_best-selling_Nintendo_Switch_video_games", "table_idx": 1, "add_col": {"Console": "Nintendo Switch"}, "rename_col": {"Title": "Game", "Copies sold": "Copies sold (million)"}, "drop_col": ["As of"], "replace_regex": {"Copies sold (million)": [r"(?i)\s*million", ""]}, "format_date": ["Release date"]},
            {"url": "https://en.wikipedia.org/wiki/List_of_best-selling_Xbox_One_video_games", "table_idx": 1, "add_col": {"Console": "Xbox One"}, "rename_col": {"Copies sold": "Copies sold (million)"}, "replace_regex": {"Copies sold (million)": [r"(?i)\s*million", ""]}, "format_date": ["Release date"]}
        ]
    },
    # {
    #     "id": "trial_union_6",
    #     "type": "UNION",
    #     "query": "List the Olympic medalists in swimming for both men and women in the 50m freestyle.",
    #     "primary_key": ["Games", "Category"],
    #     "sources": [
    #         {"url": "https://en.wikipedia.org/wiki/List_of_Olympic_medalists_in_swimming_(men)", "table_idx": 1, "add_col": {"Category": "Men"}},
    #         {"url": "https://en.wikipedia.org/wiki/List_of_Olympic_medalists_in_swimming_(women)", "table_idx": 1, "add_col": {"Category": "Women"}}
    #     ]
    # },
    # {
    #     "id": "trial_union_7",
    #     "type": "UNION",
    #     "query": "List the winners of the Grammy Award for Record of the Year and Song of the Year.",
    #     "primary_key": ["Year", "Category"],
    #     "sources": [
    #         {"url": "https://en.wikipedia.org/wiki/Grammy_Award_for_Record_of_the_Year", "table_idx": 2, "add_col": {"Category": "Record of the Year"}},
    #         {"url": "https://en.wikipedia.org/wiki/Grammy_Award_for_Song_of_the_Year", "table_idx": 2, "add_col": {"Category": "Song of the Year"}}
    #     ]
    # }
]

for task in tasks:
    print(f"\nProcessing task: {task['id']}")
    dfs = []
    success = True
    
    for idx, src in enumerate(task['sources']):
        df = get_table_from_url(src['url'], table_idx=src.get('table_idx', -1))
        if df is None:
            print(f"Failed to fetch table from {src['url']}")
            success = False
            break
            
        # Specialized filtering of rows before annotations are stripped out
        if 'filter_pattern' in src:
            pat = src['filter_pattern']
            
            if 'Year' in df.columns:
                valid_rows = []
                # Group by year while maintaining order
                for year, group in df.groupby('Year', sort=False):
                    # In Wikipedia Academy Award tables, the winner(s) are always listed first for each year.
                    # A tie means the first N rows share the winner symbol.
                    for _, row in group.iterrows():
                        row_str = row.astype(str).str.cat()
                        import re
                        if re.search(pat, row_str):
                            valid_rows.append(row)
                        else:
                            # As soon as we hit a row without the winner pattern (a nominee), we stop collecting for this year
                            break
                orig_len = len(df)
                df = pd.DataFrame(valid_rows)
                print(f"Dropped {orig_len - len(df)} nominees failing consecutive pattern {pat}")
            else:
                # Fallback if no Year column (simple regex mask)
                mask = df.astype(str).apply(lambda row: row.str.contains(pat, regex=True).any(), axis=1)
                dropped_count = len(df) - mask.sum()
                df = df[mask].reset_index(drop=True)
                print(f"Dropped {dropped_count} rows failing pattern {pat}")
                
        # Optional raw regex filtering to DROP matching rows
        if 'drop_pattern' in src:
            pat = src['drop_pattern']
            mask = df.astype(str).apply(lambda row: row.str.contains(pat, regex=True).any(), axis=1)
            dropped = mask.sum()
            df = df[~mask].reset_index(drop=True)
            print(f"Dropped {dropped} rows matching drop_pattern {pat}")
            
        # Optional year range filtering
        if 'year_range' in src:
            min_y, max_y = src['year_range']
            if 'Year' in df.columns:
                extracted_years = df['Year'].astype(str).str.extract(r'(\d{4})')[0].astype(float)
                mask = (extracted_years >= min_y) & (extracted_years <= max_y)
                dropped = len(df) - mask.sum()
                df = df[mask].reset_index(drop=True)
                print(f"Dropped {dropped} rows outside year range {min_y}-{max_y}")

        df = clean_df(df)
        
        # Track original dimensions
        src['original_rows'] = df.shape[0]
        src['original_cols'] = df.shape[1]
        
        # Optional renaming of columns for UNION alignment
        if 'rename_col' in src:
            df = df.rename(columns=src['rename_col'])
            
        if 'numeric_strip_paren' in src:
            for col in src['numeric_strip_paren']:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.split('(').str[0].str.replace(',', '').str.strip()
                    
        if 'drop_col' in src:
            for col in src['drop_col']:
                if col in df.columns:
                    df = df.drop(columns=[col])
                    
        if 'replace_regex' in src:
            for col, replacement in src['replace_regex'].items():
                if col in df.columns:
                    target_str, new_str = replacement
                    df[col] = df[col].astype(str).str.replace(target_str, new_str, regex=True)
                    
        if 'format_date' in src:
            for col in src['format_date']:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%Y-%m-%d')
                    
        if 'min_val_filter' in src:
            filter_cfg = src['min_val_filter']
            col = filter_cfg['col']
            min_val = filter_cfg['min']
            if col in df.columns:
                nums = df[col].astype(str).str.replace(',', '').str.extract(r'(\d+(?:\.\d+)?)')[0].astype(float)
                mask = nums >= min_val
                dropped = len(df) - mask.sum()
                df = df[mask].reset_index(drop=True)
                print(f"Dropped {dropped} rows below minimum {min_val} in {col}")
            
        # for unions, add the category column
        if 'add_col' in src:
            for k, v in src['add_col'].items():
                df[k] = v
                
        # Save source file
        src_path = f"{OUTPUT_DIR}/{task['id']}_src_{idx}.csv"
        df.to_csv(src_path, index=False)
        src['file_path'] = src_path
                
        dfs.append(df)
        print(f"  Source {idx} ready: {df.shape[0]} rows, {df.shape[1]} cols")
        
    if not success:
        continue
        
    final_df = None
    try:
        if task['type'] == 'JOIN':
            key1 = task['sources'][0]['key']
            key2 = task['sources'][1]['key']
            if key1 not in dfs[0].columns:
                print(f"Key {key1} not found in first table columns: {dfs[0].columns.tolist()}")
                success = False
            elif key2 not in dfs[1].columns:
                print(f"Key {key2} not found in second table columns: {dfs[1].columns.tolist()}")
                success = False
            else:
                final_df = fuzzy_merge(dfs[0], dfs[1], key1, key2)
        elif task['type'] == 'UNION':
            # Align columns by finding common ones or just concat
            final_df = pd.concat(dfs, ignore_index=True)
                
    except Exception as e:
        print(f"Error processing {task['id']}: {e}")
        success = False

    if not success or final_df is None or final_df.empty:
        print(f"Failed to generate valid final dataframe for {task['id']}")
        continue

    print(f"Rows before dropping N/A: {len(final_df)}")
    
    # Calculate and print missing statistics BEFORE dropping N/A rows
    missing_stats = final_df.isnull().sum() / len(final_df) * 100
    print("Missing value percentage per column:")
    for col, missing_pct in missing_stats.items():
        print(f"  {col}: {missing_pct:.2f}%")
        
    # Drop columns that are mostly empty (>80% missing) to avoid nuking the dataset on dropna
    cols_to_drop_missing = [col for col, missing_pct in missing_stats.items() if missing_pct > 80]
    if cols_to_drop_missing:
        final_df = final_df.drop(columns=cols_to_drop_missing)
        print(f"Dropped columns due to >80% missing values: {cols_to_drop_missing}")

    # Automatically identify and drop aggregate/summary rows using LLM on the primary key
    if not final_df.empty and task['type'] == 'JOIN':
        key1 = task['sources'][0]['key']
        if key1 in final_df.columns:
            unique_keys = final_df[key1].dropna().unique().tolist()
            agg_keys = detect_aggregate_keys_with_llm(unique_keys)
            if agg_keys:
                final_df = final_df[~final_df[key1].isin(agg_keys)]
                print(f"Dropped aggregate rows from {key1} using LLM classification: {agg_keys}")

    # Drop any N/A rows to maintain high data quality
    final_df = final_df.dropna()
    print(f"Rows after dropping N/A rules: {len(final_df)}")
    if final_df.empty:
        print(f"Dropped all rows for {task['id']} due to NAs.")
        continue

    # Clean up strings: reduce continuous whitespaces and drop "(Tie)" suffixes
    import re
    for col in final_df.columns:
        if final_df[col].dtype == 'object':
            # Remove lifespans or birth years like (1884-1949), (1884–1949), (b. 1951)
            final_df[col] = final_df[col].astype(str).apply(lambda x: re.sub(r'\s*\((?:b\.\s*\d{4}|\d{4}.*?\d{4})\)', '', x))
            final_df[col] = final_df[col].apply(lambda x: re.sub(r'\s+', ' ', x))
            final_df[col] = final_df[col].apply(lambda x: re.sub(r'(?i)\s*\(Tie\)\s*', '', x))
            final_df[col] = final_df[col].str.strip()

    # Groupby primary key to merge duplicate rows (e.g. ties or multiple films per year)
    pk = task.get('primary_key', [])
    valid_pk = [p for p in pk if p in final_df.columns]
    if valid_pk and len(final_df) > 0:
        agg_dict = {}
        for col in final_df.columns:
            if col not in valid_pk:
                # Merge by joining unique non-null values with " & "
                agg_dict[col] = lambda x: ' & '.join(pd.Series(x).dropna().astype(str).unique())
        if agg_dict:
            final_df = final_df.groupby(valid_pk, as_index=False).agg(agg_dict)
            print(f"Grouped by primary key {valid_pk}. Rows reduced to {len(final_df)}")

    # LLM Refinement
    print("Refining query and columns via LLM...")
    user_fb = task.get("feedback", "")
    new_cols, new_query, new_pks, rows_to_drop = refine_with_llm(final_df, task['query'], task['type'], task['primary_key'], user_feedback=user_fb)
    
    if len(new_cols) == len(final_df.columns):
        final_df.columns = new_cols
        cols_to_drop = [c for c in final_df.columns if str(c).strip().upper() == "DROP"]
        if cols_to_drop:
            final_df = final_df.drop(columns=cols_to_drop)
            print(f"Dropped {len(cols_to_drop)} columns via LLM.")
            
    if rows_to_drop:
        valid_indices = [r for r in rows_to_drop if r in final_df.index]
        if valid_indices:
            final_df = final_df.drop(index=valid_indices)
            print(f"Dropped {len(valid_indices)} rows via LLM feedback instructions.")

    task['query'] = new_query

    # Identify unique primary keys left after LLM formatting
    pk = new_pks
    if len(pk) == 1 and pk[0] not in final_df.columns:
        # LLM changed the PK! Fallback to the first column 
        pk = [final_df.columns[0]]
    else:
        # Just in case the LLM dropped the intended composite pk column
        pk = [p for p in pk if p in final_df.columns]
        if not pk:
            pk = [final_df.columns[0]]
            
    # Reorder columns so primary key(s) are strictly first
    other_cols = [c for c in final_df.columns if c not in pk]
    final_df = final_df[pk + other_cols]

    # Save to CSV
    csv_path = f"{OUTPUT_DIR}/{task['id']}.csv"
    final_df.to_csv(csv_path, index=False)
            
    final_rows, final_cols = final_df.shape
    
    # Write to cfg dict
    cfg_entry = {
        "final": {
            "table_name": task['id'],
            "query": task['query'],
            "columns": list(final_df.columns),
            "primary_key": pk,
            "shape": f"{final_rows} rows, {final_cols} cols"
        },
        "source": []
    }
    
    for idx, src in enumerate(task['sources']):
        cfg_entry['source'].append({
            "id": idx,
            "url": src['url'],
            "type": task['type'],
            "original_shape": f"{src.get('original_rows', 'N/A')} rows, {src.get('original_cols', 'N/A')} cols",
            "file_path": src['file_path']
        })
        
    dataset_cfg.append(cfg_entry)

with open(f"{OUTPUT_DIR}/cfg.json", "w") as f:
    json.dump({"tables": dataset_cfg}, f, indent=4)
    
print("\nDataset generation complete!")
