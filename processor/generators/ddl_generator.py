"""
generators/ddl_generator.py

Generates DDL commands:
  CREATE / DROP TABLE, COLLECTION, INDEX, TEXT INDEX,
  ALTER TABLE, VIEW, STORED PROCEDURE, TRIGGER
"""

from __future__ import annotations
import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ...nlp_processor import NLPProcessor


class DDLGenerator:

    def __init__(self, processor: "NLPProcessor"):
        self._p = processor

    # ── INDEX ─────────────────────────────────────────────────────────────

    def create_index(self, text: str) -> Optional[str]:
        m = re.search(
            r'create\s+index\s+(\w+)\s+on\s+(\w+)\s*\(?\s*([\w,\s]+)\s*\)?', text
        )
        if not m:
            return None
        idx_name, table, cols_raw = m.group(1), m.group(2), m.group(3).strip()
        col_str = ", ".join(c.strip() for c in cols_raw.split(","))
        prefix = self._p.get_prefix(table) if table in self._p.get_known_tables() else "SQL"
        return f"{prefix} CREATE INDEX {idx_name} ON {table} ({col_str})"

    def drop_index(self, text: str) -> Optional[str]:
        m = re.search(r'drop\s+index\s+(\w+)', text)
        if m:
            return f"SQL DROP INDEX {m.group(1)}"
        return None

    def create_text_index(self, text: str) -> Optional[str]:
        m = re.search(
            r'create\s+text\s+index\s+(\w+)\s+on\s+(\w+)\s*\(?\s*([\w.]+)\s*\)?', text
        )
        if m:
            return f"NOSQL CREATE TEXT INDEX {m.group(1)} ON {m.group(2)} ({m.group(3)})"
        return None

    def drop_text_index(self, text: str) -> Optional[str]:
        m = re.search(r'drop\s+text\s+index\s+(\w+)', text)
        if m:
            return f"NOSQL DROP TEXT INDEX {m.group(1)}"
        return None

    # ── TABLE / COLLECTION ────────────────────────────────────────────────

    def create_table(self, text: str) -> Optional[str]:
        # 1. Extract table name
        m = re.search(r'create\s+table\s+(\w+)', text)
        if not m:
            return None
        table_name = m.group(1)

        # 2. Extract column list
        after_table = text[m.end():].strip()
        col_names = []
        # Look for column list after "with" or in parentheses
        col_list_str = None
        m_with = re.search(r'with\s+(.+?)(?:\s+where|\s+having|\s+order|\s+limit|$)', after_table, re.IGNORECASE)
        if m_with:
            col_list_str = m_with.group(1)
        else:
            m_paren = re.search(r'\((.+?)\)', after_table)
            if m_paren:
                col_list_str = m_paren.group(1)
            else:
                # No delimiter – treat all remaining words as column names (stop at common keywords)
                col_list_str = re.split(r'\s+(?:where|having|order|limit|into|values)', after_table)[0]

        if col_list_str:
            # Split by commas or whitespace
            if ',' in col_list_str:
                parts = [p.strip() for p in col_list_str.split(',')]
            else:
                parts = col_list_str.split()
            for part in parts:
                # Remove punctuation, keep only words
                col = re.sub(r'[^\w]', '', part)
                if col and col.lower() not in ('and', 'or', 'with', 'table', 'create'):
                    col_names.append(col)
        else:
            col_names = []

        if not col_names:
            col_names = ['id', 'name']  # fallback

        # 3. Helper to infer type and constraints from column name
        def infer_column_def(col: str) -> str:
            col_lower = col.lower()
            # Primary key detection
            if col_lower == 'id' or col_lower.endswith('_id'):
                return f"{col} INT PRIMARY KEY"
            # Numeric columns
            if col_lower in ('age', 'quantity', 'count', 'stock', 'year', 'month', 'day', 'rating', 'score'):
                return f"{col} INT"
            if col_lower in ('price', 'amount', 'total', 'cost', 'revenue', 'balance', 'rate', 'tax'):
                return f"{col} REAL"
            # Boolean / flag columns
            if col_lower in ('is_active', 'is_deleted', 'has_shipped', 'flag', 'enabled', 'disabled'):
                return f"{col} BOOLEAN"
            # Date / time columns
            if col_lower in ('created_at', 'updated_at', 'timestamp', 'date', 'time', 'datetime'):
                return f"{col} TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            # Text columns (default)
            # Mark 'name', 'title', 'description' as NOT NULL
            if col_lower in ('name', 'title', 'description', 'email', 'username', 'address'):
                return f"{col} TEXT NOT NULL"
            return f"{col} TEXT"

        # 4. Build column definitions
        col_defs = [infer_column_def(col) for col in col_names]

        # 5. Create final command
        col_def_str = ", ".join(col_defs)
        return f"SQL CREATE TABLE {table_name} ({col_def_str})"

    def create_collection(self, text: str) -> Optional[str]:
        m = re.search(r'create\s+collection\s+(\w+)', text)
        if m:
            return f"NOSQL CREATE COLLECTION {m.group(1)}"
        return None

    def drop_table(self, text: str) -> Optional[str]:
        m = re.search(r'drop\s+table\s+(\w+)', text)
        if m:
            return f"SQL DROP TABLE {m.group(1)}"
        return None

    def drop_collection(self, text: str) -> Optional[str]:
        m = re.search(r'drop\s+collection\s+(\w+)', text)
        if m:
            return f"NOSQL DROP COLLECTION {m.group(1)}"
        return None

    # ── ALTER TABLE ───────────────────────────────────────────────────────

    def alter_table(self, text: str) -> Optional[str]:
        table = self._p.get_table(text)

        # ADD COLUMN [with default]
        m = re.search(
            r'add\s+column\s+(\w+)\s+(\w+)(?:\s+default\s+(.+))?', text
        )
        if m and table:
            col, typ = m.group(1), m.group(2).upper()
            default = m.group(3)
            if default:
                default = "CURRENT_TIMESTAMP" if "current_timestamp" in default.lower() else default
                return f"SQL ALTER TABLE {table} ADD {col} {typ} DEFAULT {default}"
            return f"SQL ALTER TABLE {table} ADD {col} {typ}"

        # RENAME COLUMN
        m = re.search(r'rename\s+column\s+(\w+)\s+to\s+(\w+)', text)
        if m and table:
            return f"SQL ALTER TABLE {table} RENAME COLUMN {m.group(1)} TO {m.group(2)}"

        # DROP COLUMN
        m = re.search(r'drop\s+column\s+(\w+)', text)
        if m and table:
            return f"SQL ALTER TABLE {table} DROP COLUMN {m.group(1)}"

        # RENAME TABLE
        m = re.search(r'rename\s+table\s+(\w+)\s+to\s+(\w+)', text)
        if m:
            return f"SQL ALTER TABLE {m.group(1)} RENAME TO {m.group(2)}"

        # ALTER COLUMN DEFAULT
        m = re.search(r'alter\s+column\s+(\w+)\s+set\s+default\s+(.+)', text)
        if m and table:
            return f"SQL ALTER TABLE {table} ALTER COLUMN {m.group(1)} SET DEFAULT {m.group(2)}"

        m = re.search(r'alter\s+column\s+(\w+)\s+drop\s+default', text)
        if m and table:
            return f"SQL ALTER TABLE {table} ALTER COLUMN {m.group(1)} DROP DEFAULT"

        return None

    # ── VIEW ──────────────────────────────────────────────────────────────

    def create_view(self, text: str) -> Optional[str]:
        m = re.search(r'create\s+view\s+(\w+)\s+as\s+(.+)', text)
        if m:
            return f"SQL CREATE VIEW {m.group(1)} AS {m.group(2)}"
        return None

    def drop_view(self, text: str) -> Optional[str]:
        m = re.search(r'drop\s+view\s+(\w+)', text)
        if m:
            return f"SQL DROP VIEW {m.group(1)}"
        return None

    # ── STORED PROCEDURE ─────────────────────────────────────────────────

    def create_procedure(self, text: str) -> Optional[str]:
        m = re.search(
            r'create\s+procedure\s+(\w+)\s*(?:\(([^)]*)\))?\s+as\s+(.+)', text
        )
        if m:
            proc   = m.group(1)
            params = m.group(2) or ""
            body   = m.group(3)
            return f"SQL CREATE PROCEDURE {proc} ({params}) AS {body}"
        return None

    def drop_procedure(self, text: str) -> Optional[str]:
        m = re.search(r'drop\s+procedure\s+(\w+)', text)
        if m:
            return f"SQL DROP PROCEDURE {m.group(1)}"
        return None

    # ── TRIGGER ───────────────────────────────────────────────────────────

    def create_trigger(self, text: str) -> Optional[str]:
        m = re.search(
            r'create\s+trigger\s+(\w+)\s+(before|after)\s+(insert|update|delete)'
            r'\s+on\s+(\w+)\s+for\s+each\s+row\s+begin\s+(.+?)\s+end',
            text, re.DOTALL | re.IGNORECASE,
        )
        if m:
            name, timing, event, table, stmt = (
                m.group(1), m.group(2).upper(), m.group(3).upper(),
                m.group(4), m.group(5).strip(),
            )
            return (
                f"SQL CREATE TRIGGER {name} {timing} {event} "
                f"ON {table} FOR EACH ROW BEGIN {stmt} END"
            )
        return None

    def drop_trigger(self, text: str) -> Optional[str]:
        m = re.search(r'drop\s+trigger\s+(\w+)', text)
        if m:
            return f"SQL DROP TRIGGER {m.group(1)}"
        return None