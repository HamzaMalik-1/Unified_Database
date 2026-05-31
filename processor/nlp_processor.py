# backend/processor/nlp_processor.py
import re
from typing import Optional, List, Dict, Any, Tuple
from word2number import w2n

class NLPProcessor:
    OLLAMA_MODEL = "phi35:latest"   # or "coder:latest" or "mini-coder:latest"
    OLLAMA_URL = "http://localhost:11434/api/generate"

    # Column name mapping for common synonyms
    COLUMN_MAPPING = {
        "sales": "amount",
        "revenue": "amount",
        "price": "price",
        "cost": "cost",
        "quantity": "quantity",
        "qty": "quantity"
    }

    # Date word to SQL interval mapping (simple)
    DATE_KEYWORDS = {
        "today": "CURRENT_DATE",
        "yesterday": "CURRENT_DATE - 1",
        "tomorrow": "CURRENT_DATE + 1",
        "last week": "CURRENT_DATE - 7",
        "last month": "CURRENT_DATE - 30",
        "last year": "CURRENT_DATE - 365",
        "now": "CURRENT_TIMESTAMP",
        "current time": "CURRENT_TIMESTAMP",
        "current_timestamp": "CURRENT_TIMESTAMP"
    }

    def __init__(self, catalog):
        self.catalog = catalog

    def process(self, text: str) -> Optional[str]:
        command = self._rule_based(text)
        if command:
            return command
        return self._llm_fallback(text)

    # ==================== Rule‑based processing ====================
    def _rule_based(self, text: str) -> Optional[str]:
        text = text.lower().strip()
        # Specific patterns first
        if "create index" in text:
            return self._generate_create_index(text)
        if "drop index" in text:
            return self._generate_drop_index(text)
        if "create text index" in text:
            return self._generate_create_text_index(text)
        if "drop text index" in text:
            return self._generate_drop_text_index(text)
        if "alter table" in text:
            return self._generate_alter_table(text)
        if "create view" in text:
            return self._generate_create_view(text)
        if "drop view" in text:
            return self._generate_drop_view(text)
        if "create procedure" in text:
            return self._generate_create_procedure(text)
        if "drop procedure" in text:
            return self._generate_drop_procedure(text)
        if "create trigger" in text:
            return self._generate_create_trigger(text)
        if "drop trigger" in text:
            return self._generate_drop_trigger(text)
        # Then general intents
        intent = self._classify_intent(text)
        if not intent:
            return None
        if intent == "SELECT":
            return self._generate_select(text)
        elif intent == "INSERT":
            return self._generate_insert(text)
        elif intent == "UPDATE":
            return self._generate_update(text)
        elif intent == "DELETE":
            return self._generate_delete(text)
        elif intent == "CREATE_TABLE":
            return self._generate_create_table(text)
        elif intent == "CREATE_COLLECTION":
            return self._generate_create_collection(text)
        elif intent == "DROP_TABLE":
            return self._generate_drop_table(text)
        elif intent == "DROP_COLLECTION":
            return self._generate_drop_collection(text)
        return None

    # ---------- Intent classification ----------
    def _classify_intent(self, text: str) -> Optional[str]:
        select_keywords = ["show", "list", "find", "get", "retrieve", "select"]
        for kw in select_keywords:
            if kw in text:
                return "SELECT"
        insert_keywords = ["add", "insert", "create new", "store", "new"]
        for kw in insert_keywords:
            if kw in text:
                return "INSERT"
        update_keywords = ["update", "change", "modify", "set"]
        for kw in update_keywords:
            if kw in text:
                return "UPDATE"
        delete_keywords = ["delete", "remove", "drop"]
        for kw in delete_keywords:
            if kw in text:
                if "table" in text or "collection" in text:
                    if "table" in text:
                        return "DROP_TABLE"
                    else:
                        return "DROP_COLLECTION"
                return "DELETE"
        if "create table" in text:
            return "CREATE_TABLE"
        if "create collection" in text:
            return "CREATE_COLLECTION"
        return None

    # ---------- Table/collection extraction ----------
    def _get_known_tables(self) -> List[str]:
        try:
            sql_engine = self.catalog.get_current_sql_engine()
            tables = list(sql_engine.storage.data["tables"].keys())
        except:
            tables = []
        try:
            nosql_engine = self.catalog.get_current_nosql_engine()
            collections = list(nosql_engine.storage.data["collections"].keys())
            tables.extend(collections)
        except:
            pass
        return tables

    def _extract_table(self, text: str) -> Optional[str]:
        tables = self._get_known_tables()
        for t in tables:
            if re.search(rf'\b{t.lower()}\b', text):
                return t
        for t in tables:
            if t.lower() in text:
                return t
        return None

    def _get_column_names(self, table: str) -> List[str]:
        try:
            sql_engine = self.catalog.get_current_sql_engine()
            schema = sql_engine.storage.get_schema(table)
            if schema:
                return [col["name"] for col in schema]
        except:
            pass
        return []

    def _map_column_name(self, col: str) -> str:
        return self.COLUMN_MAPPING.get(col, col)

    # ---------- Column projection extraction ----------
    def _extract_columns(self, text: str) -> Optional[List[str]]:
        table = self._extract_table(text)
        if not table:
            return None
        parts = text.split(table)
        if len(parts) < 2:
            return None
        before = parts[0]
        known_cols = self._get_column_names(table)
        common = ["id", "name", "age", "email", "city", "amount", "price", "quantity"]
        candidates = re.findall(r'\b([a-z_]+)\b', before)
        cols = [c for c in candidates if c in known_cols or c in common]
        if cols:
            return list(dict.fromkeys(cols))
        return None

    def _extract_distinct(self, text: str) -> bool:
        return "distinct" in text or "unique" in text

    # ---------- Condition parsing (enhanced with NOT, NULL, dates, OR, NOT BETWEEN) ----------
    def _extract_conditions(self, text: str) -> Optional[str]:
        # Helper to convert date expressions
        def parse_date_expression(expr: str) -> str:
            expr = expr.strip()
            for word, sql_expr in self.DATE_KEYWORDS.items():
                if word in expr:
                    return sql_expr
            return f"'{expr}'"

        # First, check for OR at the top level (simple split on " or ")
        # We'll later combine with AND if OR is present
        if " or " in text:
            # Split on " or " but not inside parentheses (we'll assume simple for now)
            parts = re.split(r'\s+or\s+', text)
            if len(parts) > 1:
                conds = [self._extract_conditions(p) for p in parts]
                conds = [c for c in conds if c]
                if conds:
                    return " OR ".join(conds)

        # Special "with id" case
        id_match = re.search(r"with\s+id\s+(\d+)", text)
        if id_match:
            value = id_match.group(1)
            try:
                value = str(w2n.word_to_num(value))
            except:
                pass
            return f"id = {value}"

        conditions = []
        # NULL handling
        null_patterns = [
            (r"(\w+)\s+is\s+(?:missing|empty|null)", r"\1 IS NULL"),
            (r"(\w+)\s+is\s+not\s+(?:missing|empty|null)", r"\1 IS NOT NULL"),
            (r"no\s+(\w+)", r"\1 IS NULL"),
            (r"without\s+(\w+)", r"\1 IS NULL"),
        ]
        for pattern, repl in null_patterns:
            match = re.search(pattern, text)
            if match:
                field = match.group(1)
                conditions.append(re.sub(pattern, repl, match.group(0)).strip())

        # NOT BETWEEN
        not_between_match = re.search(r'(\w+)\s+not\s+between\s+(\d+)\s+and\s+(\d+)', text)
        if not_between_match:
            col = not_between_match.group(1)
            low = not_between_match.group(2)
            high = not_between_match.group(3)
            conditions.append(f"{col} NOT BETWEEN {low} AND {high}")

        # BETWEEN
        between_match = re.search(r'(\w+)\s+between\s+(\d+)\s+and\s+(\d+)', text)
        if between_match:
            col = between_match.group(1)
            low = between_match.group(2)
            high = between_match.group(3)
            conditions.append(f"{col} BETWEEN {low} AND {high}")

        # IN / NOT IN
        in_match = re.search(r'(\w+)\s+(not\s+)?in\s+\(?([\d,\s]+)\)?', text)
        if in_match:
            col = in_match.group(1)
            not_flag = in_match.group(2) is not None
            vals = [v.strip() for v in in_match.group(3).split(',')]
            vals_str = ','.join(vals)
            if not_flag:
                conditions.append(f"{col} NOT IN ({vals_str})")
            else:
                conditions.append(f"{col} IN ({vals_str})")

        # LIKE / NOT LIKE
        like_match = re.search(r'(\w+)\s+(not\s+)?like\s+[\'"]?([^\'"]+)[\'"]?', text)
        if like_match:
            col = like_match.group(1)
            not_flag = like_match.group(2) is not None
            pattern = like_match.group(3)
            if not_flag:
                conditions.append(f"{col} NOT LIKE '{pattern}'")
            else:
                conditions.append(f"{col} LIKE '{pattern}'")

        # Date comparisons
        date_match = re.search(r'(\w+)\s+(after|before)\s+([\d\-]+|\w+)', text)
        if date_match:
            col = date_match.group(1)
            op = ">" if date_match.group(2) == "after" else "<"
            date_expr = parse_date_expression(date_match.group(3))
            conditions.append(f"{col} {op} {date_expr}")

        between_date_match = re.search(r'(\w+)\s+between\s+([\d\-]+|\w+)\s+and\s+([\d\-]+|\w+)', text)
        if between_date_match:
            col = between_date_match.group(1)
            d1 = parse_date_expression(between_date_match.group(2))
            d2 = parse_date_expression(between_date_match.group(3))
            conditions.append(f"{col} BETWEEN {d1} AND {d2}")

        # Standard comparisons
        patterns = [
            (r"(\w+)\s+is\s+['\"]?([^'\"]+)['\"]?", r"\1 = '\2'"),
            (r"(\w+)\s+equal[s]?\s+to\s+['\"]?([^'\"]+)['\"]?", r"\1 = '\2'"),
            (r"(\w+)\s+greater\s+than\s+(\d+)", r"\1 > \2"),
            (r"(\w+)\s+less\s+than\s+(\d+)", r"\1 < \2"),
            (r"(\w+)\s+older\s+than\s+(\d+)", r"age > \2"),
            (r"(\w+)\s+younger\s+than\s+(\d+)", r"age < \2"),
            (r"(\w+)\s+with\s+id\s+(\d+)", r"id = \2"),
            (r"id\s+(\d+)", r"id = \1"),
            (r"(\w+)\s+over\s+(\d+)", r"\1 > \2"),
        ]
        for pattern, repl in patterns:
            match = re.search(pattern, text)
            if match:
                if len(match.groups()) >= 2:
                    field = match.group(1)
                    val_raw = match.group(2)
                    try:
                        val = str(w2n.word_to_num(val_raw))
                    except:
                        val = val_raw
                    if not val.isdigit() and not val.startswith("'"):
                        val = f"'{val}'"
                    op = repl.split()[1]
                    conditions.append(f"{field} {op} {val}")
                elif len(match.groups()) == 1:
                    val = match.group(1)
                    conditions.append(f"id = {val}")

        if conditions:
            return " AND ".join(conditions)
        return None

    # ---------- JOIN detection ----------
    def _detect_join(self, text: str, table: str) -> Optional[Tuple[str, str, str]]:
        tables = self._get_known_tables()
        other_table = None
        for t in tables:
            if t != table and re.search(rf'\b{t}\b', text):
                other_table = t
                break
        if not other_table:
            return None
        join_type = "INNER"
        if "left join" in text or "left outer join" in text:
            join_type = "LEFT"
        elif "right join" in text or "right outer join" in text:
            join_type = "RIGHT"
        elif "full join" in text or "full outer join" in text:
            join_type = "FULL"
        elif "natural join" in text:
            join_type = "NATURAL"
        elif "cross join" in text:
            join_type = "CROSS"
        if re.search(r"even\s+if\s+no\s+\w+", text):
            join_type = "LEFT"
        on_match = re.search(r'on\s+([\w.]+)\s*=\s*([\w.]+)', text)
        if on_match:
            cond = f"{on_match.group(1)} = {on_match.group(2)}"
        else:
            cond = f"{table}.id = {other_table}.{table}_id"
        return (join_type, other_table, cond)

    # ---------- Subquery detection (with EXISTS) ----------
    def _detect_subquery(self, text: str, main_table: str) -> Optional[Tuple[str, str, str]]:
        if re.search(r'\b(?:who|that)\s+(?:have|has|placed|made)\s+', text):
            tables = self._get_known_tables()
            for t in tables:
                if t != main_table and re.search(rf'\b{t}\b', text):
                    inner_table = t
                    fk_col = f"{main_table}_id"
                    return ("EXISTS", None, f"SELECT 1 FROM {inner_table} WHERE {inner_table}.{fk_col} = {main_table}.id")
        if re.search(r'\b(?:who|that)\s+(?:bought|purchased|ordered)', text):
            return ("IN", "id", f"SELECT user_id FROM orders")
        tables = self._get_known_tables()
        for t in tables:
            if t != main_table and re.search(rf'\b{t}\b', text):
                return ("IN", "id", f"SELECT {main_table}_id FROM {t}")
        return None

    # ---------- Aggregation detection ----------
    def _extract_aggregates(self, text: str) -> List[Tuple[str, str, str]]:
        agg_patterns = [
            (r"count\s+of\s+(\w+)", "COUNT"),
            (r"total\s+(\w+)", "SUM"),
            (r"average\s+(\w+)", "AVG"),
            (r"avg\s+(\w+)", "AVG"),
            (r"sum\s+of\s+(\w+)", "SUM"),
            (r"minimum\s+(\w+)", "MIN"),
            (r"maximum\s+(\w+)", "MAX"),
        ]
        aggregates = []
        for pattern, func in agg_patterns:
            match = re.search(pattern, text)
            if match:
                col = match.group(1)
                col = self._map_column_name(col)
                alias = f"{col}_{func.lower()}"
                aggregates.append((func, col, alias))
        if "count of all" in text or "count of rows" in text:
            aggregates.append(("COUNT", "*", "total"))
        return aggregates

    def _extract_group_by(self, text: str) -> Optional[str]:
        group_match = re.search(r"group\s+by\s+(\w+)", text)
        if group_match:
            return group_match.group(1)
        per_match = re.search(r"per\s+(\w+)", text)
        if per_match:
            return per_match.group(1)
        return None

    def _extract_having(self, text: str) -> Optional[str]:
        having_match = re.search(r"having\s+(.+?)(?:\s+order\s+by|\s+limit|$)", text)
        if having_match:
            cond = having_match.group(1).strip()
            cond = re.sub(r'([a-z_]+)\s+is\s+', r'\1 = ', cond)
            cond = re.sub(r'greater\s+than\s+(\d+)', r'> \1', cond)
            cond = re.sub(r'less\s+than\s+(\d+)', r'< \1', cond)
            return cond
        return None

    # ---------- ORDER BY multiple columns ----------
    def _extract_order_by(self, text: str) -> Optional[List[Tuple[str, str]]]:
        order_match = re.search(r"order\s+by\s+(.+?)(?:\s+limit|\s+offset|$)", text)
        if not order_match:
            return None
        order_part = order_match.group(1).strip()
        parts = [p.strip() for p in order_part.split(',')]
        order_specs = []
        for part in parts:
            m = re.match(r'^(\w+)\s*(asc|desc)?$', part, re.IGNORECASE)
            if m:
                col = m.group(1)
                direction = m.group(2).upper() if m.group(2) else "ASC"
                order_specs.append((col, direction))
        return order_specs if order_specs else None

    def _extract_limit(self, text: str) -> Optional[int]:
        # "top N" -> LIMIT N
        top_match = re.search(r"top\s+(\d+)", text)
        if top_match:
            return int(top_match.group(1))
        # "limit N" -> LIMIT N
        limit_match = re.search(r"limit\s+(\d+)", text)
        if limit_match:
            return int(limit_match.group(1))
        # "fetch first N rows only" -> LIMIT N
        fetch_match = re.search(r"fetch\s+first\s+(\d+)\s+rows?\s+only", text)
        if fetch_match:
            return int(fetch_match.group(1))
        return None

    def _extract_offset(self, text: str) -> Optional[int]:
        offset_match = re.search(r"offset\s+(\d+)", text)
        return int(offset_match.group(1)) if offset_match else None

    # ---------- NoSQL specific operators ----------
    def _detect_nosql_operator(self, text: str) -> Optional[Tuple[str, str, Any]]:
        for op in ["push", "pull", "addtoset"]:
            match = re.search(rf"{op}\s+['\"]?(\w+)['\"]?", text)
            if match:
                value = match.group(1)
                try:
                    if value.isdigit():
                        value = int(value)
                except:
                    pass
                return (op.upper(), value)
        return None

    def _detect_contains(self, text: str) -> Optional[Tuple[str, str]]:
        match = re.search(r"contains\s+['\"]?(\w+)['\"]?", text)
        if match:
            return ("CONTAINS", match.group(1))
        return None

    # ---------- DDL operations ----------
    def _generate_create_index(self, text: str) -> Optional[str]:
        match = re.search(r"create\s+index\s+(\w+)\s+on\s+(\w+)\s*\(?\s*([\w, ]+)\s*\)?", text)
        if match:
            idx_name = match.group(1)
            table = match.group(2)
            cols = match.group(3).strip()
            col_list = [c.strip() for c in cols.split(',')]
            col_str = ", ".join(col_list)
            if table in self._get_known_tables():
                try:
                    sql_tables = list(self.catalog.get_current_sql_engine().storage.data["tables"].keys())
                    if table in sql_tables:
                        return f"SQL CREATE INDEX {idx_name} ON {table} ({col_str})"
                except:
                    pass
                return f"NOSQL CREATE INDEX {idx_name} ON {table} ({col_str})"
        return None

    def _generate_drop_index(self, text: str) -> Optional[str]:
        match = re.search(r"drop\s+index\s+(\w+)", text)
        if match:
            idx_name = match.group(1)
            return f"SQL DROP INDEX {idx_name}"
        return None

    def _generate_create_text_index(self, text: str) -> Optional[str]:
        match = re.search(r"create\s+text\s+index\s+(\w+)\s+on\s+(\w+)\s*\(?\s*([\w.]+)\s*\)?", text)
        if match:
            idx_name = match.group(1)
            coll = match.group(2)
            field = match.group(3)
            return f"NOSQL CREATE TEXT INDEX {idx_name} ON {coll} ({field})"
        return None

    def _generate_drop_text_index(self, text: str) -> Optional[str]:
        match = re.search(r"drop\s+text\s+index\s+(\w+)", text)
        if match:
            idx_name = match.group(1)
            return f"NOSQL DROP TEXT INDEX {idx_name}"
        return None

    def _generate_alter_table(self, text: str) -> Optional[str]:
        add_match = re.search(r"add\s+column\s+(\w+)\s+(\w+)(?:\s+default\s+(.+))?", text)
        if add_match:
            col = add_match.group(1)
            typ = add_match.group(2).upper()
            default_val = add_match.group(3) if add_match.group(3) else None
            table = self._extract_table(text)
            if table:
                if default_val:
                    # Check for CURRENT_TIMESTAMP
                    if "current_timestamp" in default_val.lower():
                        default_val = "CURRENT_TIMESTAMP"
                    return f"SQL ALTER TABLE {table} ADD {col} {typ} DEFAULT {default_val}"
                else:
                    return f"SQL ALTER TABLE {table} ADD {col} {typ}"
        rename_match = re.search(r"rename\s+column\s+(\w+)\s+to\s+(\w+)", text)
        if rename_match:
            old = rename_match.group(1)
            new = rename_match.group(2)
            table = self._extract_table(text)
            if table:
                return f"SQL ALTER TABLE {table} RENAME COLUMN {old} TO {new}"
        drop_match = re.search(r"drop\s+column\s+(\w+)", text)
        if drop_match:
            col = drop_match.group(1)
            table = self._extract_table(text)
            if table:
                return f"SQL ALTER TABLE {table} DROP COLUMN {col}"
        rename_table_match = re.search(r"rename\s+table\s+(\w+)\s+to\s+(\w+)", text)
        if rename_table_match:
            old = rename_table_match.group(1)
            new = rename_table_match.group(2)
            return f"SQL ALTER TABLE {old} RENAME TO {new}"
        return None

    def _generate_create_view(self, text: str) -> Optional[str]:
        match = re.search(r"create\s+view\s+(\w+)\s+as\s+(.+)", text)
        if match:
            view = match.group(1)
            query = match.group(2)
            return f"SQL CREATE VIEW {view} AS {query}"
        return None

    def _generate_drop_view(self, text: str) -> Optional[str]:
        match = re.search(r"drop\s+view\s+(\w+)", text)
        if match:
            view = match.group(1)
            return f"SQL DROP VIEW {view}"
        return None

    def _generate_create_procedure(self, text: str) -> Optional[str]:
        match = re.search(r"create\s+procedure\s+(\w+)\s*(?:\(([^)]*)\))?\s+as\s+(.+)", text)
        if match:
            proc = match.group(1)
            params = match.group(2) if match.group(2) else ""
            body = match.group(3)
            return f"SQL CREATE PROCEDURE {proc} ({params}) AS {body}"
        return None

    def _generate_drop_procedure(self, text: str) -> Optional[str]:
        match = re.search(r"drop\s+procedure\s+(\w+)", text)
        if match:
            proc = match.group(1)
            return f"SQL DROP PROCEDURE {proc}"
        return None

    def _generate_create_trigger(self, text: str) -> Optional[str]:
        match = re.search(r"create\s+trigger\s+(\w+)\s+(before|after)\s+(insert|update|delete)\s+on\s+(\w+)\s+for\s+each\s+row\s+begin\s+(.+?)\s+end", text, re.DOTALL)
        if match:
            name = match.group(1)
            timing = match.group(2).upper()
            event = match.group(3).upper()
            table = match.group(4)
            statement = match.group(5).strip()
            return f"SQL CREATE TRIGGER {name} {timing} {event} ON {table} FOR EACH ROW BEGIN {statement} END"
        return None

    def _generate_drop_trigger(self, text: str) -> Optional[str]:
        match = re.search(r"drop\s+trigger\s+(\w+)", text)
        if match:
            name = match.group(1)
            return f"SQL DROP TRIGGER {name}"
        return None

    # ---------- SELECT generation (enhanced with UNION, CTE, scalar subquery) ----------
    def _generate_select(self, text: str) -> str:
        # Check for UNION / INTERSECT / EXCEPT
        if " union " in text:
            parts = re.split(r'\s+union\s+', text)
            if len(parts) == 2:
                left = self._generate_select(parts[0])
                right = self._generate_select(parts[1])
                left_clean = re.sub(r'^(SQL|NOSQL)\s+', '', left)
                right_clean = re.sub(r'^(SQL|NOSQL)\s+', '', right)
                prefix = "SQL" if left.startswith("SQL") else "NOSQL"
                return f"{prefix} {left_clean} UNION {right_clean}"
        if " intersect " in text:
            parts = re.split(r'\s+intersect\s+', text)
            if len(parts) == 2:
                left = self._generate_select(parts[0])
                right = self._generate_select(parts[1])
                left_clean = re.sub(r'^(SQL|NOSQL)\s+', '', left)
                right_clean = re.sub(r'^(SQL|NOSQL)\s+', '', right)
                prefix = "SQL" if left.startswith("SQL") else "NOSQL"
                return f"{prefix} {left_clean} INTERSECT {right_clean}"
        if " except " in text:
            parts = re.split(r'\s+except\s+', text)
            if len(parts) == 2:
                left = self._generate_select(parts[0])
                right = self._generate_select(parts[1])
                left_clean = re.sub(r'^(SQL|NOSQL)\s+', '', left)
                right_clean = re.sub(r'^(SQL|NOSQL)\s+', '', right)
                prefix = "SQL" if left.startswith("SQL") else "NOSQL"
                return f"{prefix} {left_clean} EXCEPT {right_clean}"

        # Check for WITH (CTE)
        with_match = re.search(r'^with\s+(\w+)\s+as\s+\((.+?)\)\s+(select\s+.+)$', text, re.DOTALL)
        if with_match:
            cte_name = with_match.group(1)
            cte_query = with_match.group(2).strip()
            main_query = with_match.group(3).strip()
            inner_cmd = self._generate_select(cte_query)
            inner_clean = re.sub(r'^(SQL|NOSQL)\s+', '', inner_cmd)
            main_cmd = self._generate_select(main_query)
            main_clean = re.sub(r'^(SQL|NOSQL)\s+', '', main_cmd)
            prefix = "SQL" if inner_cmd.startswith("SQL") else "NOSQL"
            return f"{prefix} WITH {cte_name} AS ({inner_clean}) {main_clean}"

        # Normal processing
        table = self._extract_table(text) or "users"
        sql_tables = []
        try:
            sql_tables = list(self.catalog.get_current_sql_engine().storage.data["tables"].keys())
        except:
            pass
        prefix = "SQL" if table in sql_tables else "NOSQL"

        # ----- Scalar subquery detection in SELECT list -----
        # We need to parse the column list and find items like "(select ...) as alias"
        # We'll first extract the SELECT clause from the original text
        select_clause_original = ""
        select_match_orig = re.search(r'select\s+(.+?)\s+from', text, re.IGNORECASE | re.DOTALL)
        if select_match_orig:
            select_clause_original = select_match_orig.group(1).strip()
        scalar_subqueries = []
        columns_clean = []
        if select_clause_original and select_clause_original != "*":
            # Split columns by commas not inside parentheses
            cols = []
            depth = 0
            current = []
            for ch in select_clause_original:
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                elif ch == ',' and depth == 0:
                    cols.append(''.join(current).strip())
                    current = []
                    continue
                current.append(ch)
            if current:
                cols.append(''.join(current).strip())
            for col in cols:
                # Check if column starts with '(' and contains SELECT
                if col.startswith('(') and 'select' in col.lower():
                    # Extract subquery and alias
                    # Pattern: (SELECT ... ) AS alias
                    sub_match = re.match(r'\(\s*(select.+?)\s*\)\s+(?:as\s+)?(\w+)', col, re.IGNORECASE | re.DOTALL)
                    if sub_match:
                        inner_sql = sub_match.group(1).strip()
                        alias = sub_match.group(2)
                        scalar_subqueries.append((alias, inner_sql))
                        columns_clean.append(alias)
                    else:
                        # No alias? Assume whole column is subquery, not valid
                        columns_clean.append(col)
                else:
                    columns_clean.append(col)
        else:
            columns_clean = ["*"]

        # Now use the cleaned column list in the rest of the generation
        # We'll store the scalar subqueries for later attachment
        # For simplicity, we'll generate the command as usual, then replace the column list with the original? Better to integrate.

        # Continue with normal processing, but later we will inject the scalar subquery into the SELECT clause
        # We'll use the `columns_clean` as the selected columns (which may contain aliases from subqueries)

        # For joins, subqueries, etc., we keep the original logic (but we'll adjust the final SELECT clause)
        # First, detect join or subquery as before
        join_info = self._detect_join(text, table)
        if join_info:
            join_type, other_table, join_cond = join_info
            from_clause = f"{table} {join_type} JOIN {other_table} ON {join_cond}"
            command = f"{prefix} SELECT * FROM {from_clause}"
            where_cond = self._extract_conditions(text)
            if where_cond:
                command += f" WHERE {where_cond}"
            order_by = self._extract_order_by(text)
            if order_by:
                order_str = ", ".join(f"{col} {dir}" for col, dir in order_by)
                command += f" ORDER BY {order_str}"
            limit = self._extract_limit(text)
            if limit:
                command += f" LIMIT {limit}"
            return command

        subquery_info = self._detect_subquery(text, table)
        if subquery_info:
            sub_type, outer_col, inner_sql = subquery_info
            if sub_type == "IN":
                where_cond = f"{outer_col} IN ({inner_sql})"
                command = f"{prefix} SELECT * FROM {table} WHERE {where_cond}"
            elif sub_type == "EXISTS":
                where_cond = f"EXISTS ({inner_sql})"
                command = f"{prefix} SELECT * FROM {table} WHERE {where_cond}"
            else:
                command = f"{prefix} SELECT * FROM {table}"
            order_by = self._extract_order_by(text)
            if order_by:
                order_str = ", ".join(f"{col} {dir}" for col, dir in order_by)
                command += f" ORDER BY {order_str}"
            limit = self._extract_limit(text)
            if limit:
                command += f" LIMIT {limit}"
            return command

        # Regular SELECT
        columns = self._extract_columns(text)
        distinct = self._extract_distinct(text)
        select_clause = "*"
        if columns:
            select_clause = ", ".join(columns)
            if distinct:
                select_clause = "DISTINCT " + select_clause
        else:
            if distinct:
                select_clause = "DISTINCT *"

        # If we have scalar subqueries, we need to build a different select clause
        if scalar_subqueries:
            # Build select clause: regular columns + subquery aliases
            select_parts = []
            # Add regular columns (those in columns_clean that are not aliases from subqueries)
            for col in columns_clean:
                if col not in [a for a, _ in scalar_subqueries]:
                    select_parts.append(col)
            # Add subquery aliases
            for alias, _ in scalar_subqueries:
                select_parts.append(alias)
            select_clause = ", ".join(select_parts)
            # We will need to attach the subquery values later? No, we are generating the SQL with the subquery inline.
            # The column expression should be the original subquery text.
            # So we need to rebuild the select clause with the original subquery expression, not just alias.
            # Let's reconstruct from original `cols`
            select_clause = ", ".join(cols) if cols else "*"

        aggregates = self._extract_aggregates(text)
        group_by = self._extract_group_by(text)
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
        elif group_by:
            if columns:
                select_clause = ", ".join(columns)
            else:
                select_clause = group_by

        command = f"{prefix} SELECT {select_clause} FROM {table}"

        where_cond = self._extract_conditions(text)
        if where_cond:
            command += f" WHERE {where_cond}"

        if group_by:
            command += f" GROUP BY {group_by}"
            having = self._extract_having(text)
            if having:
                command += f" HAVING {having}"

        order_by = self._extract_order_by(text)
        if order_by:
            order_str = ", ".join(f"{col} {dir}" for col, dir in order_by)
            command += f" ORDER BY {order_str}"
        else:
            top = self._extract_limit(text)
            if top:
                if aggregates:
                    order_col = aggregates[0][1]
                elif columns:
                    order_col = columns[0]
                else:
                    order_col = "id"
                command += f" ORDER BY {order_col} DESC"

        limit = self._extract_limit(text)
        offset = self._extract_offset(text)
        if limit:
            if offset:
                command += f" LIMIT {offset}, {limit}"
            else:
                command += f" LIMIT {limit}"

        return command

    # ---------- INSERT, UPDATE, DELETE (unchanged, but with NOW handling) ----------
    def _generate_insert(self, text: str) -> str:
        table = self._extract_table(text) or "users"
        pairs = {}
        # Look for "now" or "current_timestamp" as a value
        if "now" in text or "current timestamp" in text:
            pairs["created_at"] = "CURRENT_TIMESTAMP"
        for match in re.finditer(r'(\w+)\s+(?:is|=)\s+[\'"]?(\w+)[\'"]?', text):
            col, val = match.group(1), match.group(2)
            pairs[col] = val
        name_match = re.search(r'named?\s+[\'"]?(\w+)[\'"]?', text)
        if name_match and "name" not in pairs:
            pairs["name"] = name_match.group(1)
        age_match = re.search(r'age\s+(\d+)', text)
        if age_match and "age" not in pairs:
            pairs["age"] = age_match.group(1)
        if pairs:
            cols = list(pairs.keys())
            vals = []
            for v in pairs.values():
                if v == "CURRENT_TIMESTAMP":
                    vals.append("CURRENT_TIMESTAMP")
                elif isinstance(v, str) and not v.isdigit():
                    vals.append(f"'{v}'")
                else:
                    vals.append(str(v))
            return f"SQL INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)})"
        else:
            return f"SQL INSERT INTO {table} DEFAULT VALUES"

    def _generate_update(self, text: str) -> str:
        table = self._extract_table(text) or "users"
        nosql_op = self._detect_nosql_operator(text)
        if nosql_op:
            op, val = nosql_op
            field_match = re.search(r'(\w+)\s+(?:push|pull|addtoset)', text)
            if field_match:
                field = field_match.group(1)
                condition = self._extract_conditions(text)
                condition_part = f" WHERE {condition}" if condition else ""
                return f"NOSQL UPDATE {table} SET {field} {op} {val}{condition_part}"
            else:
                return f"NOSQL UPDATE {table}"
        set_pairs = []
        set_matches = re.finditer(r'(\w+)\s+(?:to|=)?\s+[\'"]?(\w+)[\'"]?', text)
        for match in set_matches:
            col, val = match.group(1), match.group(2)
            if col in ["name", "age", "id", "status"]:
                set_pairs.append(f"{col} = '{val}'" if not val.isdigit() else f"{col} = {val}")
        if set_pairs:
            condition = self._extract_conditions(text)
            condition_part = f" WHERE {condition}" if condition else ""
            return f"SQL UPDATE {table} SET {', '.join(set_pairs)}{condition_part}"
        return f"SQL UPDATE {table}"

    def _generate_delete(self, text: str) -> str:
        table = self._extract_table(text) or "users"
        condition = self._extract_conditions(text)
        if condition:
            return f"SQL DELETE FROM {table} WHERE {condition}"
        else:
            return f"SQL DELETE FROM {table}"

    # ---------- Simple DDL ----------
    def _generate_create_table(self, text: str) -> str:
        match = re.search(r"create\s+table\s+(\w+)", text)
        if match:
            table = match.group(1)
            return f"SQL CREATE TABLE {table} (id INT PRIMARY KEY, name TEXT)"
        return None

    def _generate_create_collection(self, text: str) -> str:
        match = re.search(r"create\s+collection\s+(\w+)", text)
        if match:
            coll = match.group(1)
            return f"NOSQL CREATE COLLECTION {coll}"
        return None

    def _generate_drop_table(self, text: str) -> str:
        match = re.search(r"drop\s+table\s+(\w+)", text)
        if match:
            table = match.group(1)
            return f"SQL DROP TABLE {table}"
        return None

    def _generate_drop_collection(self, text: str) -> str:
        match = re.search(r"drop\s+collection\s+(\w+)", text)
        if match:
            coll = match.group(1)
            return f"NOSQL DROP COLLECTION {coll}"
        return None

    # ---------- LLM fallback ----------
    def _llm_fallback(self, text: str) -> Optional[str]:
        prompt = f"""You are a database assistant. Convert the following user request into a SQL or NoSQL command.
The command must start with either "SQL" or "NOSQL".
Only output the command, nothing else.

User request: {text}

Command:"""
        try:
            import requests
            response = requests.post(
                self.OLLAMA_URL,
                json={
                    "model": self.OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0,
                    "max_tokens": 200
                },
                timeout=30
            )
            if response.status_code == 200:
                result = response.json().get("response", "").strip()
                if result.upper().startswith("SQL") or result.upper().startswith("NOSQL"):
                    return result
            return None
        except Exception as e:
            print(f"LLM error: {e}")
            return None