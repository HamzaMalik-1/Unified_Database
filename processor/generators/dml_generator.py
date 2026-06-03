"""
generators/dml_generator.py

Generates INSERT, UPDATE, DELETE commands from natural-language text.
"""

from __future__ import annotations
import re
import json
from typing import Optional, TYPE_CHECKING, List, Tuple, Dict, Any
from ..extractors.condition_extractor import ConditionExtractor

try:
    from word2number import w2n as _w2n
    def _to_num(v: str) -> str:
        try:
            return str(_w2n.word_to_num(v))
        except Exception:
            return v
except ImportError:
    def _to_num(v: str) -> str:
        return v

if TYPE_CHECKING:
    from ...nlp_processor import NLPProcessor


def _quote(val: str) -> str:
    """Quote a value for SQL/NoSQL, unless it's a keyword or number."""
    if val in ("CURRENT_TIMESTAMP", "CURRENT_DATE", "NULL", "TRUE", "FALSE"):
        return val
    if val.lstrip("-").isdigit() or val.replace('.', '', 1).lstrip("-").isdigit():
        return val
    return f"'{val.replace(chr(39), chr(39)+chr(39))}'"


class DMLGenerator:
    def __init__(self, processor: "NLPProcessor"):
        self._p = processor

    # ------------------------------------------------------------------
    # INSERT
    # ------------------------------------------------------------------

    def insert(self, text: str) -> str:
        p = self._p
        # First try to extract table from "to X" or "into X"
        table = self._get_table_from_preposition(text, p.get_known_tables())
        if not table:
            table = p.get_table(text)   # fallback
        if not table:
            return "Error: Could not determine table name for INSERT"
        if table not in p.get_known_tables():
            return f"Error: Table/Collection '{table}' does not exist"

        prefix = p.get_prefix(table)
        known_cols = p.get_column_names(table)
        known_cols_lower = [c.lower() for c in known_cols]

        # Extract column → value pairs
        pairs = self._extract_column_value_pairs(text, known_cols_lower)

        # Special handlers
        if re.search(r'\b(now|current timestamp)\b', text):
            pairs["created_at"] = "CURRENT_TIMESTAMP"

        name_match = re.search(r'\bnamed?\s+[\'"]?([^\s\'"]+)[\'"]?', text)
        if name_match and "name" not in pairs:
            pairs["name"] = name_match.group(1)

        age_match = re.search(r'\bage\s+(\d+)\b', text)
        if age_match and "age" not in pairs:
            pairs["age"] = age_match.group(1)

        # For NoSQL (schemaless) we skip column validation – accept all pairs as valid.
        if prefix == "NOSQL":
            valid_pairs = pairs
            invalid_cols = []
        else:
            # Validate columns for SQL tables
            valid_pairs = {}
            invalid_cols = []
            for col, val in pairs.items():
                if col.lower() in known_cols_lower:
                    orig_col = next((c for c in known_cols if c.lower() == col.lower()), col)
                    valid_pairs[orig_col] = val
                else:
                    invalid_cols.append(col)

        # Build command
        if prefix == "NOSQL":
            command = self._build_nosql_insert_command(table, valid_pairs)
        else:
            if valid_pairs:
                cols_list = list(valid_pairs.keys())
                vals_list = [_quote(v) for v in valid_pairs.values()]
                command = f"{prefix} INSERT INTO {table} ({', '.join(cols_list)}) VALUES ({', '.join(vals_list)})"
            else:
                command = f"{prefix} INSERT INTO {table} DEFAULT VALUES"

        if invalid_cols:
            unique_invalid = list(dict.fromkeys(invalid_cols))
            return f"Error: Column(s) {', '.join(unique_invalid)} do not exist in table '{table}'. Generated command: {command}"
        return command

    def _get_table_from_preposition(self, text: str, known_tables: List[str]) -> Optional[str]:
        text_lower = text.lower()
        for pattern in [r'\bto\s+(\w+)\b', r'\binto\s+(\w+)\b']:
            m = re.search(pattern, text_lower)
            if m:
                candidate = m.group(1)
                if candidate in known_tables:
                    return candidate
        return None

    def _build_nosql_insert_command(self, table: str, valid_pairs: Dict[str, str]) -> str:
        flat_cols = {}
        nested_groups = {}
        for col, val in valid_pairs.items():
            if '.' in col:
                root, sub = col.split('.', 1)
                if root not in nested_groups:
                    nested_groups[root] = {}
                nested_groups[root][sub] = self._to_json_value(val)
            else:
                flat_cols[col] = val

        final_cols = list(flat_cols.keys()) + list(nested_groups.keys())
        final_vals = []
        for col in flat_cols:
            final_vals.append(_quote(flat_cols[col]))
        for root, sub_dict in nested_groups.items():
            json_str = json.dumps(sub_dict)
            json_str_single = json_str.replace('"', "'")
            final_vals.append(f"'{json_str_single}'")

        if final_cols:
            cols_part = ", ".join(final_cols)
            vals_part = ", ".join(final_vals)
            return f"NOSQL INSERT INTO {table} ({cols_part}) VALUES ({vals_part})"
        else:
            return f"NOSQL INSERT INTO {table} DEFAULT VALUES"

    def _to_json_value(self, val: str) -> Any:
        if val.lstrip('-').isdigit():
            return int(val)
        try:
            return float(val)
        except ValueError:
            return val

    # ------------------------------------------------------------------
    # EXTRACTION (handles quoted multi-word values)
    # ------------------------------------------------------------------

    def _extract_column_value_pairs(self, text: str, known_cols_lower: List[str]) -> dict:
        pairs = {}
        # Add common text fields for document/content
        common_cols = {
            "id", "name", "age", "email", "city", "address", "zip", "country",
            "price", "amount", "quantity", "product", "region", "status", "user_id",
            "content", "title", "body", "text", "description"
        }
        plausible_cols = set(known_cols_lower) | common_cols

        stopwords = {
            "a", "an", "the", "and", "or", "of", "to", "for", "with", "by",
            "on", "at", "in", "into", "from", "where", "set", "values",
            "add", "new", "user", "record", "document", "named", "called",
            "has", "have", "be", "is", "are", "was", "were", "will", "would",
            "could", "should", "may", "might", "must", "do", "does", "did",
            "can", "could", "up", "down", "out", "over", "under", "again",
            "further", "then", "once", "here", "there", "all", "any", "both",
            "each", "few", "more", "most", "other", "some", "such", "no",
            "nor", "not", "only", "own", "same", "so", "than", "that", "thus",
            "very", "just", "but", "do", "make", "them", "they", "this", "these",
            "those", "we", "our", "us", "you", "your", "he", "she", "it", "they",
            "me", "him", "her", "it", "them"
        }

        # Tokenize respecting quoted strings
        def tokenize(s: str) -> List[str]:
            tokens = []
            in_quote = False
            quote_char = None
            current = []
            for ch in s:
                if (ch == "'" or ch == '"') and not in_quote:
                    in_quote = True
                    quote_char = ch
                    current.append(ch)
                elif ch == quote_char and in_quote:
                    current.append(ch)
                    tokens.append(''.join(current))
                    current = []
                    in_quote = False
                elif ch.isspace() and not in_quote:
                    if current:
                        tokens.append(''.join(current))
                        current = []
                else:
                    current.append(ch)
            if current:
                tokens.append(''.join(current))
            return tokens

        tokens = tokenize(text)
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in stopwords or len(token) < 2:
                i += 1
                continue
            if token in plausible_cols and i + 1 < len(tokens):
                next_token = tokens[i + 1]
                is_number = next_token.replace('.', '', 1).lstrip('-').isdigit()
                if is_number or (next_token not in stopwords and len(next_token) >= 1):
                    col = token
                    val = next_token.strip("'\"")
                    if col not in pairs:
                        pairs[col] = _to_num(val)
                    i += 2
                    continue
            i += 1

        # Explicit "col = value" or "col is value"
        for match in re.finditer(r'\b(\w+)\s+(?:is|=)\s+([^\s]+)', text):
            col, val = match.group(1), match.group(2)
            if col in plausible_cols and col not in pairs:
                pairs[col] = _to_num(val.strip("'\""))

        # "with col1 val1 col2 val2 ..."
        with_match = re.search(r'\bwith\s+(.+?)(?=\s+(?:where|and|into|values)|\s*$)', text, re.IGNORECASE)
        if with_match:
            clause = with_match.group(1)
            tokens2 = tokenize(clause)
            i = 0
            while i + 1 < len(tokens2):
                col = tokens2[i].rstrip(',')
                raw_val = tokens2[i + 1].strip("'\"")
                if col in plausible_cols and col not in pairs:
                    pairs[col] = _to_num(raw_val)
                i += 2

        return pairs

    # ------------------------------------------------------------------
    # UPDATE (improved)
    # ------------------------------------------------------------------

    def update(self, text: str) -> str:
        p = self._p
        # Extract table name (handle singular/plural)
        table = self._get_table_from_preposition(text, p.get_known_tables())
        if not table:
            table = p.get_table(text)
        if not table:
            return "Error: Could not determine table name for UPDATE"
        if table not in p.get_known_tables():
            # Try singular/plural mapping
            known_tables = p.get_known_tables()
            if table.endswith('s') and table[:-1] in known_tables:
                table = table[:-1]
            elif table + 's' in known_tables:
                table = table + 's'
            else:
                return f"Error: Table/Collection '{table}' does not exist"

        prefix = p.get_prefix(table)

        # NoSQL array operators
        nosql_op = self._detect_nosql_array_op(text)
        if nosql_op:
            op, val = nosql_op
            field_m = re.search(r'\b(\w+)\s+(?:push|pull|addtoset)\b', text)
            field = field_m.group(1) if field_m else "field"
            where = p.conditions.extract(text)
            where_part = f" WHERE {where}" if where else ""
            return f"NOSQL UPDATE {table} SET {field} {op} {val}{where_part}"

        # Find "set" keyword
        set_match = re.search(r'\bset\b', text, re.IGNORECASE)
        if not set_match:
            return f"{prefix} UPDATE {table}"

        before_set = text[:set_match.start()].strip()
        after_set = text[set_match.end():].strip()

        # Extract condition from before_set (if any)
        condition = None
        cond_in_before = re.search(r'\b(?:with|whose|where)\s+(.+)$', before_set, re.IGNORECASE)
        if cond_in_before:
            cond_text = cond_in_before.group(1).strip()
            m_id = re.match(r'id\s+(\d+)', cond_text, re.IGNORECASE)
            if m_id:
                condition = f"id = {m_id.group(1)}"
            else:
                parts = cond_text.split()
                if len(parts) >= 2:
                    col = parts[0]
                    val = parts[1]
                    condition = f"{col} = {val}"
                else:
                    from ..extractors.condition_extractor import ConditionExtractor
                    cond_ext = ConditionExtractor()
                    condition = cond_ext.extract(cond_text)

        # If no condition in before_set, look in after_set
        if condition is None:
            assignments_text = after_set
            cond_match = re.search(r'\b(where|with|whose)\b', after_set, re.IGNORECASE)
            if cond_match:
                cond_start = cond_match.start()
                condition_text = after_set[cond_start:].strip()
                assignments_text = after_set[:cond_start].strip()
                for kw in ('where', 'with', 'whose'):
                    if condition_text.lower().startswith(kw):
                        condition_text = condition_text[len(kw):].strip()
                        break
                cond_parts = condition_text.split()
                if len(cond_parts) == 2:
                    col = cond_parts[0]
                    val = cond_parts[1]
                    condition = f"{col} = {val}"
                elif len(cond_parts) == 3 and cond_parts[1] == '=':
                    condition = condition_text
                else:
                    from ..extractors.condition_extractor import ConditionExtractor
                    cond_ext = ConditionExtractor()
                    condition = cond_ext.extract(condition_text)
            else:
                assignments_text = after_set

            # Extract assignments
            known_cols = p.get_column_names(table)
            known_cols_lower = [c.lower() for c in known_cols]
            assignments = self._extract_column_value_pairs(assignments_text, known_cols_lower)
        else:
            # Condition already found in before_set, extract assignments from after_set
            assignments = self._extract_column_value_pairs(after_set, [])

        # Validate assignments
        if prefix == "NOSQL":
            valid_assignments = assignments
        else:
            known_cols = p.get_column_names(table)
            known_cols_lower = [c.lower() for c in known_cols]
            valid_assignments = {}
            for col, val in assignments.items():
                if col.lower() in known_cols_lower:
                    orig_col = next((c for c in known_cols if c.lower() == col.lower()), col)
                    valid_assignments[orig_col] = val
                else:
                    return f"Error: Column '{col}' does not exist in table '{table}'"
            if not valid_assignments:
                return f"Error: No valid columns to update in table '{table}'"

        # Build command
        set_clause = ", ".join(f"{col} = {_quote(val)}" for col, val in valid_assignments.items())
        command = f"{prefix} UPDATE {table} SET {set_clause}"
        if condition:
            command += f" WHERE {condition}"
        return command

    def _extract_update_condition(self, text: str) -> Optional[str]:
        """Legacy method – kept for compatibility."""
        cond_ext = ConditionExtractor()
        return cond_ext.extract(text)

    def _extract_update_assignments(self, text: str) -> Dict[str, str]:
        """Legacy method – kept for compatibility."""
        return self._extract_column_value_pairs(text, [])

    # ------------------------------------------------------------------
    # DELETE (unchanged)
    # ------------------------------------------------------------------

    def delete(self, text: str) -> str:
        p = self._p
        table = self._get_table_from_preposition(text, p.get_known_tables())
        if not table:
            table = p.get_table(text)
        if not table:
            return "Error: Could not determine table name for DELETE"
        if table not in p.get_known_tables():
            return f"Error: Table/Collection '{table}' does not exist"

        prefix = p.get_prefix(table)
        where = p.conditions.extract(text)
        if where:
            return f"{prefix} DELETE FROM {table} WHERE {where}"
        return f"{prefix} DELETE FROM {table}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_nosql_array_op(self, text: str) -> Optional[Tuple[str, str]]:
        for op in ("push", "pull", "addtoset"):
            m = re.search(rf'\b{op}\s+[\'"]?([^\s\'"]+)[\'"]?', text)
            if m:
                val = m.group(1)
                try:
                    if val.isdigit():
                        val = int(val)
                except Exception:
                    pass
                return (op.upper(), str(val))
        return None