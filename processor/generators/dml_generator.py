"""
generators/dml_generator.py

Generates INSERT, UPDATE, DELETE commands from natural-language text.
"""

from __future__ import annotations
import re
from typing import Optional, TYPE_CHECKING

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
    if val in ("CURRENT_TIMESTAMP", "CURRENT_DATE", "NULL", "TRUE", "FALSE"):
        return val
    if val.lstrip("-").isdigit():
        return val
    return f"'{val}'"


class DMLGenerator:

    def __init__(self, processor: "NLPProcessor"):
        self._p = processor

    # ──────────────────────────────────────────────────────────────────────
    #  INSERT
    # ──────────────────────────────────────────────────────────────────────

    def insert(self, text: str) -> str:
        p = self._p
        table = p.get_table(text)
        if not table:
            return "Error: Could not determine table name for INSERT"
        if table not in p.get_known_tables():
            return f"Error: Table/Collection '{table}' does not exist"

        prefix = p.get_prefix(table)
        pairs: dict[str, str] = {}

        # Timestamp shorthand
        if re.search(r'\b(now|current timestamp)\b', text):
            pairs["created_at"] = "CURRENT_TIMESTAMP"

        # "col is/= val" pairs
        for m in re.finditer(r'\b(\w+)\s+(?:is|=)\s+[\'"]?(\w+)[\'"]?', text):
            col, val = m.group(1), m.group(2)
            if col.lower() not in {"where", "and", "or", "set"}:
                pairs[col] = _to_num(val)

        # "named X"
        m = re.search(r'\bnamed?\s+[\'"]?(\w+)[\'"]?', text)
        if m and "name" not in pairs:
            pairs["name"] = m.group(1)

        # "age N"
        m = re.search(r'\bage\s+(\d+)\b', text)
        if m and "age" not in pairs:
            pairs["age"] = m.group(1)

        if not pairs:
            return f"{prefix} INSERT INTO {table} DEFAULT VALUES"

        cols = list(pairs.keys())
        vals = [_quote(v) for v in pairs.values()]
        return f"{prefix} INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)})"

    # ──────────────────────────────────────────────────────────────────────
    #  UPDATE
    # ──────────────────────────────────────────────────────────────────────

    def update(self, text: str) -> str:
        p = self._p
        table = p.get_table(text)
        if not table:
            return "Error: Could not determine table name for UPDATE"
        if table not in p.get_known_tables():
            return f"Error: Table/Collection '{table}' does not exist"

        prefix = p.get_prefix(table)

        # NoSQL array operators: PUSH / PULL / ADDTOSET
        nosql_op = self._detect_nosql_array_op(text)
        if nosql_op:
            op, val = nosql_op
            field_m = re.search(r'\b(\w+)\s+(?:push|pull|addtoset)\b', text)
            field = field_m.group(1) if field_m else "field"
            where = p.conditions.extract(text)
            where_part = f" WHERE {where}" if where else ""
            return f"NOSQL UPDATE {table} SET {field} {op} {val}{where_part}"

        # Standard SET pairs
        set_pairs: list[str] = []
        for m in re.finditer(r'\b(\w+)\s+(?:to|=)?\s+[\'"]?(\w+)[\'"]?', text):
            col, val = m.group(1), m.group(2)
            if col.lower() in {"name", "age", "id", "status", "email", "city", "price",
                                "amount", "quantity", "description"}:
                val = _to_num(val)
                set_pairs.append(f"{col} = {_quote(val)}")

        if set_pairs:
            where = p.conditions.extract(text)
            where_part = f" WHERE {where}" if where else ""
            return f"{prefix} UPDATE {table} SET {', '.join(set_pairs)}{where_part}"

        return f"{prefix} UPDATE {table}"

    # ──────────────────────────────────────────────────────────────────────
    #  DELETE
    # ──────────────────────────────────────────────────────────────────────

    def delete(self, text: str) -> str:
        p = self._p
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

    # ──────────────────────────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _detect_nosql_array_op(self, text: str):
        for op in ("push", "pull", "addtoset"):
            m = re.search(rf'\b{op}\s+[\'"]?(\w+)[\'"]?', text)
            if m:
                val: str | int = m.group(1)
                try:
                    val = int(val) if str(val).isdigit() else val  # type: ignore[assignment]
                except Exception:
                    pass
                return (op.upper(), val)
        return None
