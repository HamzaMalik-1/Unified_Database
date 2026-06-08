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
        m = re.search(r'create\s+table\s+(\w+)', text)
        if not m:
            return None
        table_name = m.group(1)

        after_table = text[m.end():].strip()
        col_names = []
        col_list_str = None
        m_with = re.search(r'with\s+(.+?)(?:\s+where|\s+having|\s+order|\s+limit|$)', after_table, re.IGNORECASE)
        if m_with:
            col_list_str = m_with.group(1)
        else:
            m_paren = re.search(r'\((.+?)\)', after_table)
            if m_paren:
                col_list_str = m_paren.group(1)
            else:
                col_list_str = re.split(r'\s+(?:where|having|order|limit|into|values)', after_table)[0]

        if col_list_str:
            if ',' in col_list_str:
                parts = [p.strip() for p in col_list_str.split(',')]
            else:
                parts = col_list_str.split()
            for part in parts:
                col = re.sub(r'[^\w]', '', part)
                if col and col.lower() not in ('and', 'or', 'with', 'table', 'create'):
                    col_names.append(col)
        else:
            col_names = []

        if not col_names:
            col_names = ['id', 'name']

        def infer_column_def(col: str) -> str:
            col_lower = col.lower()
            if col_lower == 'id' or col_lower.endswith('_id'):
                return f"{col} SERIAL PRIMARY KEY"
            if col_lower in ('age', 'quantity', 'count', 'stock', 'year', 'month', 'day', 'rating', 'score'):
                return f"{col} INT"
            if col_lower in ('price', 'amount', 'total', 'cost', 'revenue', 'balance', 'rate', 'tax'):
                return f"{col} REAL"
            if col_lower in ('is_active', 'is_deleted', 'has_shipped', 'flag', 'enabled', 'disabled'):
                return f"{col} BOOLEAN"
            if col_lower in ('created_at', 'updated_at', 'timestamp', 'date', 'time', 'datetime'):
                return f"{col} TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            if col_lower in ('name', 'title', 'description', 'email', 'username', 'address'):
                return f"{col} TEXT NOT NULL"
            return f"{col} TEXT"

        col_defs = [infer_column_def(col) for col in col_names]
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
        text_lower = text.lower()
        known_tables = self._p.get_known_tables()

        # 1. RENAME TABLE (must be first, no table lookup needed)
        m_rename_table = re.search(r'rename\s+table\s+(\w+)\s+to\s+(\w+)', text_lower)
        if m_rename_table:
            old_table = m_rename_table.group(1)
            new_table = m_rename_table.group(2)
            # Optional: verify old_table exists
            if (old_table in known_tables or
                (old_table.endswith('s') and old_table[:-1] in known_tables) or
                (old_table + 's' in known_tables)):
                return f"SQL ALTER TABLE {old_table} RENAME TO {new_table}"
            return None

        # For all other ALTER operations we need a table name
        table = self._get_table_for_alter(text_lower, known_tables)
        if not table:
            return None

        # 2. RENAME COLUMN
        m_rename = re.search(r'rename\s+column\s+(\w+)\s+to\s+(\w+)', text_lower)
        if m_rename:
            old_col = m_rename.group(1)
            new_col = m_rename.group(2)
            return f"SQL ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col}"

        # 3. ADD COLUMN
        m_add = re.search(r'add\s+column\s+(\w+)\s+(\w+)', text_lower)
        if m_add:
            col = m_add.group(1)
            typ = m_add.group(2).upper()
            default_match = re.search(r'default\s+(.+)', text_lower)
            if default_match:
                default_val = default_match.group(1).strip()
                if "current_timestamp" in default_val:
                    default_val = "CURRENT_TIMESTAMP"
                return f"SQL ALTER TABLE {table} ADD {col} {typ} DEFAULT {default_val}"
            return f"SQL ALTER TABLE {table} ADD {col} {typ}"

        # 4. DROP COLUMN
        m_drop = re.search(r'drop\s+column\s+(\w+)', text_lower)
        if m_drop:
            col = m_drop.group(1)
            return f"SQL ALTER TABLE {table} DROP COLUMN {col}"

        # 5. ALTER COLUMN SET DEFAULT
        m_set_default = re.search(r'alter\s+column\s+(\w+)\s+set\s+default\s+(.+)', text_lower)
        if m_set_default:
            return f"SQL ALTER TABLE {table} ALTER COLUMN {m_set_default.group(1)} SET DEFAULT {m_set_default.group(2)}"

        # 6. ALTER COLUMN DROP DEFAULT
        m_drop_default = re.search(r'alter\s+column\s+(\w+)\s+drop\s+default', text_lower)
        if m_drop_default:
            return f"SQL ALTER TABLE {table} ALTER COLUMN {m_drop_default.group(1)} DROP DEFAULT"

        # 7. ADD CHECK (optional)
        m_add_check = re.search(r'add\s+check\s+\((.+?)\)', text_lower)
        if m_add_check:
            expr = m_add_check.group(1)
            return f"SQL ALTER TABLE {table} ADD CHECK ({expr})"

        return None

    def _get_table_for_alter(self, text_lower: str, known_tables: list) -> Optional[str]:
        """
        Extract table name for ALTER operations (excluding RENAME TABLE).
        Uses the processor's TableExtractor plus "in"/"from" patterns.
        """
        # Try "in <td>"
        in_match = re.search(r'\bin\s+(\w+)\b', text_lower)
        if in_match:
            cand = in_match.group(1)
            if cand in known_tables:
                return cand
            if cand.endswith('s') and cand[:-1] in known_tables:
                return cand[:-1]
            if cand + 's' in known_tables:
                return cand + 's'

        # Try "from <td>"
        from_match = re.search(r'\bfrom\s+(\w+)\b', text_lower)
        if from_match:
            cand = from_match.group(1)
            if cand in known_tables:
                return cand
            if cand.endswith('s') and cand[:-1] in known_tables:
                return cand[:-1]
            if cand + 's' in known_tables:
                return cand + 's'

        # Use the main TableExtractor (via processor.get_table)
        table = self._p.get_table(text_lower)
        if table and table in known_tables:
            return table

        # Last resort: any word that matches a known table
        words = re.findall(r'\b[a-z_][a-z0-9_]*\b', text_lower)
        for w in words:
            if w in known_tables:
                return w
            if w.endswith('s') and w[:-1] in known_tables:
                return w[:-1]
            if w + 's' in known_tables:
                return w + 's'
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
            proc = m.group(1)
            params = m.group(2) or ""
            body = m.group(3)
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
            return f"SQL CREATE TRIGGER {name} {timing} {event} ON {table} FOR EACH ROW BEGIN {stmt} END"
        return None

    def drop_trigger(self, text: str) -> Optional[str]:
        m = re.search(r'drop\s+trigger\s+(\w+)', text)
        if m:
            return f"SQL DROP TRIGGER {m.group(1)}"
        return None