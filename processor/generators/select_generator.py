"""
generators/select_generator.py

Builds the final SQL / NOSQL SELECT command string from extracted parts.
Handles: simple select, aggregates, GROUP BY, HAVING, JOINs, sub-queries,
         scalar sub-queries, UNION / INTERSECT / EXCEPT, WITH (CTE), implicit JOINs,
         "who have" subqueries, explicit IN subqueries, CONTAINS, and "with column value".
"""

from __future__ import annotations
import re
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ...nlp_processor import NLPProcessor


class SelectGenerator:

    def __init__(self, processor: "NLPProcessor"):
        self._p = processor

    # ──────────────────────────────────────────────────────────────────────
    #  Public entry point
    # ──────────────────────────────────────────────────────────────────────

    def generate(self, text: str) -> Optional[str]:
        p = self._p

        # NEW: Handle "with column value" pattern (e.g., "list users with name ali")
        direct_result = self._handle_with_condition(text)
        if direct_result:
            return direct_result

        # Handle CONTAINS queries
        direct_result = self._handle_contains(text)
        if direct_result:
            return direct_result

        # 0. "show users with id in (select user_id from orders)"
        direct_result = self._handle_in_subquery(text)
        if direct_result:
            return direct_result

        # 1. "show users who have orders"
        direct_result = self._handle_who_have(text)
        if direct_result:
            return direct_result

        # 2. "show users and orders" / "show users orders"
        direct_result = self._handle_implicit_join(text)
        if direct_result:
            return direct_result

        # 3. "show count of sales by product having count > Z"
        direct_result = self._handle_aggregate_count(text)
        if direct_result:
            return direct_result

        # 4. "show average age group by city" etc.
        direct_result = self._handle_aggregate_general(text)
        if direct_result:
            return direct_result

        # ------------------------------------------------------------------
        #  Pre‑process: rewrite "count X ..." or "show count X ..." to
        #  "select count(*) from X ..." (fallback, may not be needed now)
        # ------------------------------------------------------------------
        original_text = text
        text = self._preprocess_aggregate_query(text)

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
            text, re.DOTALL | re.IGNORECASE,
        )
        if with_m:
            cte_name = with_m.group(1)
            inner_sql = with_m.group(2).strip()
            main_sql = with_m.group(3).strip()
            # CTEs are a SQL feature; force prefix to SQL
            return f"SQL WITH {cte_name} AS ({inner_sql}) {main_sql}"

        # ── Strip trailing "order by" and "limit/offset" for join detection ──
        base_text, order_by_str, limit_str = self._strip_trailing_clauses(text)

        # Special handling for "bottom N" (not captured by standard limit extraction)
        if not limit_str:
            bottom_match = re.search(r'\bbottom\s+(\d+)\b', text.lower())
            if bottom_match:
                limit_str = bottom_match.group(1)
                order_by_str = ""   # will be set later to ASC

        # ── Table identification using stripped text ───────────────────────
        table = p.get_table(base_text)
        if not table:
            return None

        known_tables = p.get_known_tables()
        if table not in known_tables:
            return f"Error: Table/Collection '{table}' does not exist"

        prefix = p.get_prefix(table)

        # ── JOIN (using stripped text) ─────────────────────────────────────
        join_info = p.select_parts.extract_join(base_text, table, known_tables)
        if join_info:
            join_type, other_table, join_cond = join_info
            cmd = f"{prefix} SELECT * FROM {table} {join_type} JOIN {other_table} ON {join_cond}"
            where = p.conditions.extract(base_text)
            if where:
                cmd += f" WHERE {where}"
            if order_by_str:
                cmd += f" ORDER BY {order_by_str}"
            if limit_str:
                cmd += f" LIMIT {limit_str}"
            return cmd

        # ── Subquery (EXISTS / IN) using stripped text ─────────────────────
        sub = self._detect_subquery(base_text, table, known_tables)
        if sub:
            sub_type, outer_col, inner_sql = sub
            if sub_type == "EXISTS":
                cmd = f"{prefix} SELECT * FROM {table} WHERE EXISTS ({inner_sql})"
            else:  # IN
                cmd = f"{prefix} SELECT * FROM {table} WHERE {outer_col} IN ({inner_sql})"
            if order_by_str:
                cmd += f" ORDER BY {order_by_str}"
            if limit_str:
                cmd += f" LIMIT {limit_str}"
            return cmd

        # ── Regular SELECT (using stripped text) ───────────────────────────
        known_cols  = p.get_column_names(table)
        columns     = p.select_parts.extract_columns(base_text, table, known_cols)
        distinct    = p.select_parts.extract_distinct(base_text)
        aggregates  = p.select_parts.extract_aggregates(base_text)
        group_by    = p.select_parts.extract_group_by(base_text)

        # ── FALLBACK: if group_by is still None but we have aggregates and text contains "by X" ──
        if not group_by and aggregates:
            # Look for pattern "by <word>" where <word> is likely a column name
            by_match = re.search(r'\bby\s+([a-z_][a-z0-9_]*)\b', base_text.lower())
            if by_match:
                candidate = by_match.group(1)
                # verify candidate is a known column (or just accept it)
                if candidate in known_cols or candidate in ['region', 'city', 'product', 'category']:
                    group_by = candidate

        # Build SELECT clause
        if aggregates:
            agg_parts = []
            for func, col, alias in aggregates:
                if col == "*":
                    agg_parts.append(f"{func}(*) AS {alias}")
                else:
                    agg_parts.append(f"{func}({col}) AS {alias}")
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

        # WHERE (from base text)
        where = p.conditions.extract(base_text)
        if where:
            cmd += f" WHERE {where}"

        # GROUP BY / HAVING
        if group_by:
            cmd += f" GROUP BY {group_by}"
            having = p.select_parts.extract_having(base_text)
            if having:
                # Convert "count > 1" to "count(*) > 1" if needed
                having = self._normalize_having(having)
                cmd += f" HAVING {having}"

        # ── Handle "top N" and "bottom N" (if no explicit ORDER BY) ─────────
        if not order_by_str and limit_str:
            text_lower = original_text.lower()
            # Determine default sort column (prefer 'id', else first column)
            default_sort_col = "id"
            if known_cols:
                for col in known_cols:
                    if col.lower() == "id":
                        default_sort_col = col
                        break
                else:
                    default_sort_col = known_cols[0]

            if re.search(r'\btop\s+\d+\b', text_lower):
                order_by_str = f"{default_sort_col} DESC"
            elif re.search(r'\bbottom\s+\d+\b', text_lower):
                order_by_str = f"{default_sort_col} ASC"

        # Append ORDER BY and LIMIT
        if order_by_str:
            cmd += f" ORDER BY {order_by_str}"
        if limit_str:
            cmd += f" LIMIT {limit_str}"

        return cmd

    # ------------------------------------------------------------------
    #  NEW DIRECT HANDLER for "with column value" pattern
    #  e.g., "list users with name ali" → SQL SELECT * FROM users WHERE name = 'ali'
    # ------------------------------------------------------------------
    def _handle_with_condition(self, text: str) -> Optional[str]:
        """
        Handles queries like:
          - "list users with name ali"
          - "show products with price 100"
          - "get customers with email john@example.com"
        Returns a command with WHERE equality condition.
        """
        text_lower = text.lower().strip()
        # Remove leading command words: list, show, get, find, retrieve
        for prefix in ("list ", "show ", "get ", "find ", "retrieve "):
            if text_lower.startswith(prefix):
                rest = text_lower[len(prefix):].strip()
                break
        else:
            rest = text_lower

        # Pattern: <table> with <column> <value>
        # Value can be a word, quoted string, or number
        match = re.match(r'^(\w+)\s+with\s+(\w+)\s+([^\s]+?)(?:\s+and\s+|\s*$)', rest)
        if not match:
            # Allow value to be multiple words (like email with dots)
            match = re.match(r'^(\w+)\s+with\s+(\w+)\s+(.+)$', rest)
        if not match:
            return None

        table_candidate = match.group(1)
        column = match.group(2)
        value_raw = match.group(3).strip().strip('"\'')

        # Resolve table name (handle singular/plural)
        known_tables = self._p.get_known_tables()
        table = None
        if table_candidate in known_tables:
            table = table_candidate
        else:
            singular = table_candidate.rstrip('s')
            if singular in known_tables:
                table = singular
            elif table_candidate + 's' in known_tables:
                table = table_candidate + 's'

        if table is None:
            return None

        # Verify column exists (optional, but good)
        known_cols = self._p.get_column_names(table)
        if column not in known_cols:
            # If column not found, maybe it's a synonym? For now, still proceed but log?
            # We'll still generate the query but with the given column.
            pass

        prefix = self._p.get_prefix(table)
        # Quote the value if it's not a number
        if value_raw.isdigit():
            quoted_value = value_raw
        else:
            quoted_value = f"'{value_raw}'"

        return f"{prefix} SELECT * FROM {table} WHERE {column} = {quoted_value}"

    # ------------------------------------------------------------------
    #  DIRECT HANDLER for CONTAINS (NoSQL array/string containment)
    # ------------------------------------------------------------------
    def _handle_contains(self, text: str) -> Optional[str]:
        """
        Handles queries like:
          - "show nosql_users where hobbies contains 'reading'"
          - "show nosql_users where name contains 'ali'"
          - "show nosql_users whose hobbies contains 'reading'"
        Returns a NOSQL SELECT command with CONTAINS condition.
        """
        text_lower = text.lower().strip()
        # Remove leading "show " or "list " or "get "
        if text_lower.startswith("show "):
            rest = text_lower[5:].strip()
        elif text_lower.startswith("list "):
            rest = text_lower[5:].strip()
        elif text_lower.startswith("get "):
            rest = text_lower[4:].strip()
        else:
            return None

        # Patterns: table where <field> contains <value>
        # or table whose <field> contains <value>
        match = re.match(r'^(\w+)\s+(?:where|whose)\s+(\w+)\s+contains\s+[\'"]?([^\'"]+)[\'"]?$', rest)
        if not match:
            # Also allow "contains" without quotes around value
            match = re.match(r'^(\w+)\s+(?:where|whose)\s+(\w+)\s+contains\s+(\w+)$', rest)
        if not match:
            return None

        table_candidate = match.group(1)
        field = match.group(2)
        value = match.group(3).strip("'\"")

        # Resolve table name
        known_tables = self._p.get_known_tables()
        table = None
        if table_candidate in known_tables:
            table = table_candidate
        else:
            singular = table_candidate.rstrip('s')
            if singular in known_tables:
                table = singular
            elif table_candidate + 's' in known_tables:
                table = table_candidate + 's'

        if table is None:
            return None

        prefix = self._p.get_prefix(table)
        # Properly quote the value (assuming string)
        quoted_value = f"'{value}'"
        return f"{prefix} SELECT * FROM {table} WHERE {field} CONTAINS {quoted_value}"

    # ------------------------------------------------------------------
    #  DIRECT HANDLER for "show users with id in (select user_id from order)"
    # ------------------------------------------------------------------
    def _handle_in_subquery(self, text: str) -> Optional[str]:
        """
        Handles queries like:
          - "show users with id in (select user_id from orders)"
        Returns SQL command with IN subquery.
        """
        text_lower = text.lower().strip()
        # Remove leading "show " or "list "
        if text_lower.startswith("show "):
            rest = text_lower[5:].strip()
        elif text_lower.startswith("list "):
            rest = text_lower[5:].strip()
        else:
            return None

        # Pattern: <table1> with id in (select <column> from <table2>)
        match = re.match(r'^(\w+)\s+with\s+id\s+in\s*\(\s*select\s+(\w+)\s+from\s+(\w+)\s*\)$', rest)
        if not match:
            return None

        main_table = match.group(1)
        sub_column = match.group(2)
        sub_table = match.group(3)
        known_tables = self._p.get_known_tables()
        if main_table not in known_tables or sub_table not in known_tables:
            return None

        prefix = self._p.get_prefix(main_table)
        return f"{prefix} SELECT * FROM {main_table} WHERE id IN (SELECT {sub_column} FROM {sub_table})"

    # ------------------------------------------------------------------
    #  DIRECT HANDLER for "show users who have orders"
    # ------------------------------------------------------------------
    def _handle_who_have(self, text: str) -> Optional[str]:
        """
        Handles queries like:
          - "show users who have orders"
          - "list customers who have purchased"
        Returns an EXISTS subquery command.
        Always returns a command (even if foreign key column not found) for valid patterns.
        """
        text_lower = text.lower().strip()
        # Remove leading "show " or "list "
        if text_lower.startswith("show "):
            rest = text_lower[5:].strip()
        elif text_lower.startswith("list "):
            rest = text_lower[5:].strip()
        else:
            return None

        match = re.match(r'^(\w+)\s+(?:who|that)\s+have\s+(\w+)$', rest)
        if not match:
            return None

        main_table = match.group(1)
        child_table = match.group(2)
        known_tables = self._p.get_known_tables()
        if main_table not in known_tables or child_table not in known_tables:
            return None

        child_cols = self._p.get_column_names(child_table)
        candidates = [
            f"{child_table}.{main_table}_id",
            f"{child_table}.{main_table.rstrip('s')}_id",
            f"{child_table}.user_id",
        ]
        seen = set()
        unique_candidates = []
        for c in candidates:
            col_name = c.split('.')[1]
            if col_name not in seen:
                seen.add(col_name)
                unique_candidates.append(c)
        unique_candidates.append(f"{child_table}.id")

        child_column = None
        for cand in unique_candidates:
            col = cand.split('.')[1]
            if col in child_cols:
                child_column = cand
                break
        if child_column is None:
            if main_table == "users" and "user_id" in [c.split('.')[1] for c in unique_candidates]:
                child_column = unique_candidates[1]
            elif f"{child_table}.{main_table}_id" in unique_candidates:
                child_column = f"{child_table}.{main_table}_id"
            else:
                child_column = f"{child_table}.{main_table}_id"

        prefix = self._p.get_prefix(main_table)
        return f"{prefix} SELECT * FROM {main_table} WHERE EXISTS (SELECT 1 FROM {child_table} WHERE {child_column} = {main_table}.id)"

    # ------------------------------------------------------------------
    #  DIRECT HANDLER for implicit joins: "show users and orders" etc.
    # ------------------------------------------------------------------
    def _handle_implicit_join(self, text: str) -> Optional[str]:
        """
        Handles queries like:
          - "show users and orders"
          - "show users orders"
          - "list users and orders"
        Returns an INNER JOIN command.
        """
        text_lower = text.lower().strip()
        if text_lower.startswith("show "):
            rest = text_lower[5:].strip()
        elif text_lower.startswith("list "):
            rest = text_lower[5:].strip()
        else:
            return None

        if " and " in rest:
            parts = rest.split(" and ")
        else:
            words = rest.split()
            if len(words) >= 2:
                parts = [words[0], words[1]]
            else:
                return None

        if len(parts) != 2:
            return None

        table1 = parts[0]
        table2 = parts[1]
        known_tables = self._p.get_known_tables()
        if table1 not in known_tables or table2 not in known_tables:
            return None

        fk_candidates = [
            (f"{table1}.id", f"{table2}.{table1}_id"),
            (f"{table2}.id", f"{table1}.{table2}_id"),
        ]
        join_cond = None
        for left, right in fk_candidates:
            if left.split('.')[1] in self._p.get_column_names(table1) and \
               right.split('.')[1] in self._p.get_column_names(table2):
                join_cond = f"{left} = {right}"
                break
        if join_cond is None:
            if table1 == "users" and table2 == "orders":
                join_cond = "users.id = orders.user_id"
            elif table1 == "orders" and table2 == "users":
                join_cond = "orders.user_id = users.id"
            else:
                return None

        prefix = self._p.get_prefix(table1)
        return f"{prefix} SELECT * FROM {table1} INNER JOIN {table2} ON {join_cond}"

    # ------------------------------------------------------------------
    #  DIRECT HANDLER for "show average age group by city"
    # ------------------------------------------------------------------
    def _handle_aggregate_general(self, text: str) -> Optional[str]:
        """
        Parses queries like:
          - "show average age group by city"
          - "show avg age group by city"
          - "show sum amount group by region"
          - "show total amount group by region"
        Returns SQL command or None.
        """
        text_lower = text.lower().strip()
        words = text_lower.split()
        if not words or words[0] != "show":
            return None
        if len(words) < 4:
            return None

        agg_map = {
            "average": "AVG",
            "avg": "AVG",
            "sum": "SUM",
            "total": "SUM",
            "count": "COUNT",
            "min": "MIN",
            "maximum": "MAX",
            "max": "MAX",
        }

        agg_word = words[1]
        if agg_word not in agg_map:
            return None
        agg_func = agg_map[agg_word]

        if len(words) < 3:
            return None
        column = words[2]

        group_by_idx = None
        for i, w in enumerate(words):
            if w == "group" and i+1 < len(words) and words[i+1] == "by":
                group_by_idx = i + 2
                break
        if group_by_idx is None or group_by_idx >= len(words):
            return None
        group_col = words[group_by_idx]

        known_tables = self._p.get_known_tables()
        candidate_table = None
        for t in known_tables:
            if t in text_lower:
                candidate_table = t
                break
        if candidate_table is None:
            for t in known_tables:
                cols = self._p.get_column_names(t)
                if column in cols and group_col in cols:
                    candidate_table = t
                    break
        if candidate_table is None:
            if known_tables:
                candidate_table = known_tables[0]
            else:
                return None

        prefix = self._p.get_prefix(candidate_table)
        alias = f"{agg_word}_{column}" if agg_word != "count" else "total"
        return f"{prefix} SELECT {agg_func}({column}) AS {alias}, {group_col} FROM {candidate_table} GROUP BY {group_col}"

    # ------------------------------------------------------------------
    #  DIRECT HANDLER for "show count X by Y" / "show count of X by Y"
    # ------------------------------------------------------------------
    def _handle_aggregate_count(self, text: str) -> Optional[str]:
        """
        Parses queries like:
          - "show count orders by product"
          - "show count of orders by product"
          - "count orders by product"
          - "show count of sales by product"
        Returns a COUNT + GROUP BY command.
        """
        text_lower = text.lower().strip()
        # Normalize: remove leading "show " if present
        if text_lower.startswith("show "):
            rest = text_lower[5:].strip()
        else:
            rest = text_lower

        # Pattern: count [of] table by <column>
        # Match optional "of"
        match = re.match(r'^count\s+(?:of\s+)?(\w+)\s+by\s+(\w+)(?:\s+having\s+count\s*>\s*(\d+))?$', rest)
        if not match:
            return None

        table_candidate = match.group(1)
        group_col = match.group(2)
        threshold = match.group(3) if match.group(3) else None

        # Resolve table name (handle singular/plural)
        known_tables = self._p.get_known_tables()
        table = None
        if table_candidate in known_tables:
            table = table_candidate
        else:
            singular = table_candidate.rstrip('s')
            if singular in known_tables:
                table = singular
            elif table_candidate + 's' in known_tables:
                table = table_candidate + 's'

        if table is None:
            return None

        prefix = self._p.get_prefix(table)
        if threshold is not None:
            return f"{prefix} SELECT COUNT(*) AS total, {group_col} FROM {table} GROUP BY {group_col} HAVING COUNT(*) > {threshold}"
        else:
            return f"{prefix} SELECT COUNT(*) AS total, {group_col} FROM {table} GROUP BY {group_col}"

    # ------------------------------------------------------------------
    #  Helper: preprocess "count X ..." or "show count X ..." to
    #  "select count(*) from X ..." (legacy, kept for safety)
    # ------------------------------------------------------------------
    def _preprocess_aggregate_query(self, text: str) -> str:
        """Rewrite 'show count orders by user_id ...' to 'select count(*) from orders ...'."""
        text_lower = text.lower().strip()
        known_tables = self._p.get_known_tables()
        count_match = re.match(r'^(?:show\s+)?count\s+(\w+)(.*)$', text_lower)
        if count_match:
            candidate_table = count_match.group(1)
            rest = count_match.group(2).strip()
            if candidate_table in known_tables:
                by_match = re.search(r'\bby\s+(\w+)', rest)
                if by_match:
                    group_col = by_match.group(1)
                    rest_without_by = rest[:by_match.start()] + rest[by_match.end():]
                    new_text = f"select count(*) from {candidate_table} {rest_without_by} group by {group_col}"
                else:
                    new_text = f"select count(*) from {candidate_table} {rest}"
                return new_text
        return text

    # ------------------------------------------------------------------
    #  Normalise HAVING clause (e.g., "count > 1" -> "count(*) > 1")
    # ------------------------------------------------------------------
    def _normalize_having(self, having: str) -> str:
        having = re.sub(r'\bcount\s+', 'count(*) ', having, flags=re.IGNORECASE)
        return having

    # ──────────────────────────────────────────────────────────────────────
    #  Helper: strip "order by" and "limit/offset" from the query
    # ──────────────────────────────────────────────────────────────────────

    def _strip_trailing_clauses(self, text: str):
        p = self._p
        order_by_list = p.select_parts.extract_order_by(text)
        order_by_str = ""
        if order_by_list:
            order_by_str = ", ".join(f"{col} {dir}" for col, dir in order_by_list)

        limit_val = p.select_parts.extract_limit(text)
        offset_val = p.select_parts.extract_offset(text)
        limit_str = ""
        if limit_val:
            if offset_val:
                limit_str = f"{offset_val}, {limit_val}"
            else:
                limit_str = str(limit_val)

        stripped = text
        order_match = re.search(r'\border\s+by\s+[^;]+?(?=\s+limit\b|\s*$)', stripped, re.IGNORECASE)
        if order_match:
            stripped = stripped[:order_match.start()] + stripped[order_match.end():]
        limit_match = re.search(r'\blimit\s+\d+(?:\s*,\s*\d+|\s+offset\s+\d+)?', stripped, re.IGNORECASE)
        if limit_match:
            stripped = stripped[:limit_match.start()] + stripped[limit_match.end():]
        offset_match = re.search(r'\boffset\s+\d+', stripped, re.IGNORECASE)
        if offset_match:
            stripped = stripped[:offset_match.start()] + stripped[offset_match.end():]

        stripped = stripped.strip()
        return stripped, order_by_str, limit_str

    # ──────────────────────────────────────────────────────────────────────
    #  Subquery detection (fallback, may not be used now)
    # ──────────────────────────────────────────────────────────────────────

    def _detect_subquery(self, text, main_table, known_tables):
        if re.search(r'\b(?:who|that)\s+(?:have|has|placed|made|bought|purchased|ordered)\b', text):
            for t in known_tables:
                if t != main_table and re.search(rf'\b{re.escape(t)}\b', text, re.IGNORECASE):
                    fk = f"{main_table}_id"
                    return ("EXISTS", None, f"SELECT 1 FROM {t} WHERE {t}.{fk} = {main_table}.id")

        for t in known_tables:
            if t != main_table and re.search(rf'\b{re.escape(t)}\b', text, re.IGNORECASE):
                return ("IN", "id", f"SELECT {main_table}_id FROM {t}")

        return None