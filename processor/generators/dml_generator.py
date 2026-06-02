"""
generators/dml_generator.py

Generates INSERT, UPDATE, DELETE commands from natural-language text.
"""

from __future__ import annotations
import re
import json
from typing import Optional, TYPE_CHECKING, List, Tuple, Dict, Any

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
        """
        Extract a table name that follows the prepositions 'to' or 'into'.
        Example: "add a sale to sales" -> returns 'sales' if it is in known_tables.
        """
        text_lower = text.lower()
        for pattern in [r'\bto\s+(\w+)\b', r'\binto\s+(\w+)\b']:
            m = re.search(pattern, text_lower)
            if m:
                candidate = m.group(1)
                if candidate in known_tables:
                    return candidate
        return None

    def _build_nosql_insert_command(self, table: str, valid_pairs: Dict[str, str]) -> str:
        """
        Build a NoSQL INSERT command, converting dotted column names into nested JSON objects.
        Example: {'id': '1', 'name': 'Alice', 'address.city': 'Boston', 'address.zip': '123'}
        becomes: INSERT INTO table (id, name, address) VALUES (1, 'Alice', {"city":"Boston","zip":123})
        """
        flat_cols = {}
        nested_groups = {}  # root -> dict of subfield -> value

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
    # EXTRACTION (unchanged)
    # ------------------------------------------------------------------

    def _extract_column_value_pairs(self, text: str, known_cols_lower: List[str]) -> dict:
        pairs = {}
        common_cols = {
            "id", "name", "age", "email", "city", "address", "zip", "country",
            "price", "amount", "quantity", "product", "region", "status", "user_id"
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

        words = re.findall(r'[^\s]+', text)
        i = 0
        while i < len(words):
            word = words[i]
            if word in stopwords or len(word) < 2:
                i += 1
                continue
            if word in plausible_cols and i + 1 < len(words):
                next_word = words[i + 1]
                is_number = next_word.replace('.', '', 1).lstrip('-').isdigit()
                if is_number or (next_word not in stopwords and len(next_word) >= 1):
                    if word not in pairs:
                        pairs[word] = _to_num(next_word)
                    i += 2
                    continue
            i += 1

        for match in re.finditer(r'\b(\w+)\s+(?:is|=)\s+([^\s]+)', text):
            col, val = match.group(1), match.group(2)
            if col in plausible_cols and col not in pairs:
                pairs[col] = _to_num(val.strip("'\""))

        with_match = re.search(r'\bwith\s+(.+?)(?=\s+(?:where|and|into|values)|\s*$)', text, re.IGNORECASE)
        if with_match:
            clause = with_match.group(1)
            tokens = re.findall(r'[^\s]+', clause)
            i = 0
            while i + 1 < len(tokens):
                col = tokens[i].rstrip(',')
                raw_val = tokens[i + 1].strip("'\"")
                if col in plausible_cols and col not in pairs:
                    pairs[col] = _to_num(raw_val)
                i += 2
        return pairs

    # ------------------------------------------------------------------
    # UPDATE
    # ------------------------------------------------------------------

    def update(self, text: str) -> str:
        p = self._p
        table = self._get_table_from_preposition(text, p.get_known_tables())
        if not table:
            table = p.get_table(text)
        if not table:
            return "Error: Could not determine table name for UPDATE"
        if table not in p.get_known_tables():
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

        set_pairs = []
        for match in re.finditer(r'\b(\w+)\s+(?:to|=)\s+([^\s]+)', text):
            col, val = match.group(1), match.group(2)
            if col.lower() in {"name", "age", "id", "status", "email", "city", "price", "amount", "quantity", "description", "user_id"}:
                val = _to_num(val.strip("'\""))
                set_pairs.append(f"{col} = {_quote(val)}")

        if set_pairs:
            where = p.conditions.extract(text)
            where_part = f" WHERE {where}" if where else ""
            return f"{prefix} UPDATE {table} SET {', '.join(set_pairs)}{where_part}"
        return f"{prefix} UPDATE {table}"

    # ------------------------------------------------------------------
    # DELETE
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