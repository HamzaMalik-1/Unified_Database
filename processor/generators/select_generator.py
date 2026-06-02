"""
generators/select_generator.py

Builds the final SQL / NOSQL SELECT command string from extracted parts.
Handles: simple select, aggregates, GROUP BY, HAVING, JOINs, sub-queries,
         scalar sub-queries, UNION / INTERSECT / EXCEPT, WITH (CTE).
"""

from __future__ import annotations
import re
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ...nlp_processor import NLPProcessor


class SelectGenerator:

    def __init__(self, processor: "NLPProcessor"):
        self._p = processor   # back-reference to access all extractors & catalog helpers

    # ──────────────────────────────────────────────────────────────────────
    #  Public entry point
    # ──────────────────────────────────────────────────────────────────────

    def generate(self, text: str) -> Optional[str]:
        p = self._p

        # ── Set-ops: UNION / INTERSECT / EXCEPT ──────────────────────────
        for keyword in ("union", "intersect", "except"):
            split_pattern = rf'\s+{keyword}\s+'
            if f" {keyword} " in text:
                parts = re.split(split_pattern, text, maxsplit=1)
                if len(parts) == 2:
                    left  = self.generate(parts[0])
                    right = self.generate(parts[1])
                    if left and right:
                        lc = re.sub(r'^(SQL|NOSQL)\s+', '', left)
                        rc = re.sub(r'^(SQL|NOSQL)\s+', '', right)
                        prefix = "SQL" if left.startswith("SQL") else "NOSQL"
                        return f"{prefix} {lc} {keyword.upper()} {rc}"

        # ── CTE: WITH <name> AS (<inner>) <main> ─────────────────────────
        with_m = re.match(
            r'^with\s+(\w+)\s+as\s+\((.+?)\)\s+(select\s+.+)$',
            text, re.DOTALL,
        )
        if with_m:
            cte_name   = with_m.group(1)
            cte_query  = with_m.group(2).strip()
            main_query = with_m.group(3).strip()
            inner = self.generate(cte_query)
            main  = self.generate(main_query)
            if inner and main:
                ic = re.sub(r'^(SQL|NOSQL)\s+', '', inner)
                mc = re.sub(r'^(SQL|NOSQL)\s+', '', main)
                prefix = "SQL" if inner.startswith("SQL") else "NOSQL"
                return f"{prefix} WITH {cte_name} AS ({ic}) {mc}"
            return None

        # ── Table identification ──────────────────────────────────────────
        table = p.get_table(text)
        if not table:
            return None

        known_tables = p.get_known_tables()
        if table not in known_tables:
            return f"Error: Table/Collection '{table}' does not exist"

        prefix = p.get_prefix(table)

        # ── JOIN ──────────────────────────────────────────────────────────
        join_info = p.select_parts.extract_join(text, table, known_tables)
        if join_info:
            join_type, other_table, join_cond = join_info
            cmd = f"{prefix} SELECT * FROM {table} {join_type} JOIN {other_table} ON {join_cond}"
            where = p.conditions.extract(text)
            if where:
                cmd += f" WHERE {where}"
            order = p.select_parts.extract_order_by(text)
            if order:
                cmd += " ORDER BY " + ", ".join(f"{c} {d}" for c, d in order)
            limit = p.select_parts.extract_limit(text)
            if limit:
                cmd += f" LIMIT {limit}"
            return cmd

        # ── Subquery (EXISTS / IN) ────────────────────────────────────────
        sub = self._detect_subquery(text, table, known_tables)
        if sub:
            sub_type, outer_col, inner_sql = sub
            if sub_type == "EXISTS":
                cmd = f"{prefix} SELECT * FROM {table} WHERE EXISTS ({inner_sql})"
            else:  # IN
                cmd = f"{prefix} SELECT * FROM {table} WHERE {outer_col} IN ({inner_sql})"
            order = p.select_parts.extract_order_by(text)
            if order:
                cmd += " ORDER BY " + ", ".join(f"{c} {d}" for c, d in order)
            limit = p.select_parts.extract_limit(text)
            if limit:
                cmd += f" LIMIT {limit}"
            return cmd

        # ── Regular SELECT ────────────────────────────────────────────────
        known_cols  = p.get_column_names(table)
        columns     = p.select_parts.extract_columns(text, table, known_cols)
        distinct    = p.select_parts.extract_distinct(text)
        aggregates  = p.select_parts.extract_aggregates(text)
        group_by    = p.select_parts.extract_group_by(text)

        # Build SELECT clause
        if aggregates:
            agg_parts = [
                f"{func}(*) AS {alias}" if col == "*" else f"{func}({col}) AS {alias}"
                for func, col, alias in aggregates
            ]
            select_clause = ", ".join(agg_parts)
            if group_by:
                select_clause += f", {group_by}"
        elif columns:
            select_clause = ("DISTINCT " if distinct else "") + ", ".join(columns)
        elif group_by:
            select_clause = group_by
        else:
            select_clause = "DISTINCT *" if distinct else "*"

        cmd = f"{prefix} SELECT {select_clause} FROM {table}"

        # WHERE
        where = p.conditions.extract(text)
        if where:
            cmd += f" WHERE {where}"

        # GROUP BY / HAVING
        if group_by:
            cmd += f" GROUP BY {group_by}"
            having = p.select_parts.extract_having(text)
            if having:
                cmd += f" HAVING {having}"

        # ORDER BY
        order = p.select_parts.extract_order_by(text)
        if order:
            cmd += " ORDER BY " + ", ".join(f"{c} {d}" for c, d in order)
        else:
            limit = p.select_parts.extract_limit(text)
            if limit:
                # Implicit sort for TOP N
                if aggregates:
                    order_col = aggregates[0][1] if aggregates[0][1] != "*" else "id"
                elif columns:
                    order_col = columns[0]
                else:
                    order_col = "id"
                cmd += f" ORDER BY {order_col} DESC"

        # LIMIT / OFFSET
        limit  = p.select_parts.extract_limit(text)
        offset = p.select_parts.extract_offset(text)
        if limit:
            cmd += f" LIMIT {offset}, {limit}" if offset else f" LIMIT {limit}"

        return cmd

    # ──────────────────────────────────────────────────────────────────────
    #  Subquery detection
    # ──────────────────────────────────────────────────────────────────────

    def _detect_subquery(self, text, main_table, known_tables):
        """Return (type, outer_col, inner_sql) or None."""
        # Pattern: "users who have/placed orders"
        if re.search(r'\b(?:who|that)\s+(?:have|has|placed|made|bought|purchased|ordered)\b', text):
            for t in known_tables:
                if t != main_table and re.search(rf'\b{re.escape(t)}\b', text, re.IGNORECASE):
                    fk = f"{main_table}_id"
                    return ("EXISTS", None, f"SELECT 1 FROM {t} WHERE {t}.{fk} = {main_table}.id")

        for t in known_tables:
            if t != main_table and re.search(rf'\b{re.escape(t)}\b', text, re.IGNORECASE):
                return ("IN", "id", f"SELECT {main_table}_id FROM {t}")

        return None
