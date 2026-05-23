# backend/processor/nlp_processor.py
import re
from typing import Optional, List, Dict, Any, Tuple
from word2number import w2n

class NLPProcessor:
    OLLAMA_MODEL = "phi35:latest"   # or "coder:latest" or "mini-coder:latest"
    OLLAMA_URL = "http://localhost:11434/api/generate"
    def __init__(self, catalog):
        self.catalog = catalog

    def process(self, text: str) -> Optional[str]:
        """Public method: try rule‑based, then LLM fallback."""
        # First try rule‑based
        command = self._rule_based(text)
        if command:
            return command
        # Optionally enable LLM fallback
        return self._llm_fallback(text)
        # return None

    # ==================== Rule‑based processing ====================
    def _rule_based(self, text: str) -> Optional[str]:
        text = text.lower().strip()
        # First, detect very specific patterns that override generic intents
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

    # ---------- Column projection extraction ----------
    def _extract_columns(self, text: str) -> Optional[List[str]]:
        """Extract specific column names from a phrase like 'show name, age from users'."""
        # Patterns: "show name and age" or "show name, age" or "select name, age"
        # The columns likely appear before the table name.
        table = self._extract_table(text)
        if not table:
            return None
        # Find the part before the table name
        parts = text.split(table)
        if len(parts) < 2:
            return None
        before = parts[0]
        # Look for words that are column names (known from schema) or common column names
        known_cols = self._get_column_names(table)
        # Also include common generic column names
        common = ["id", "name", "age", "email", "city", "amount", "price", "quantity"]
        candidates = re.findall(r'\b([a-z_]+)\b', before)
        # Filter candidates that are either known columns or common names
        cols = [c for c in candidates if c in known_cols or c in common]
        if cols:
            return list(dict.fromkeys(cols))  # unique order preserved
        return None

    def _extract_distinct(self, text: str) -> bool:
        return "distinct" in text or "unique" in text

    # ---------- Condition parsing (supports AND/OR, LIKE, BETWEEN, IN) ----------
    def _extract_conditions(self, text: str) -> Optional[str]:
        # First, handle special "with id" case
        id_match = re.search(r"with\s+id\s+(\d+)", text)
        if id_match:
            value = id_match.group(1)
            try:
                value = str(w2n.word_to_num(value))
            except:
                pass
            return f"id = {value}"

        # Build list of conditions
        conditions = []
        # 1. BETWEEN ... AND ...
        between_match = re.search(r'(\w+)\s+between\s+(\d+)\s+and\s+(\d+)', text)
        if between_match:
            col = between_match.group(1)
            low = between_match.group(2)
            high = between_match.group(3)
            conditions.append(f"{col} BETWEEN {low} AND {high}")

        # 2. IN list: "in (1,2,3)" or "id in 1,2,3"
        in_match = re.search(r'(\w+)\s+in\s+\(?([\d,\s]+)\)?', text)
        if in_match:
            col = in_match.group(1)
            vals = [v.strip() for v in in_match.group(2).split(',')]
            vals_str = ','.join(vals)
            conditions.append(f"{col} IN ({vals_str})")

        # 3. LIKE: "name like 'A%'"
        like_match = re.search(r'(\w+)\s+like\s+[\'"]?([^\'"]+)[\'"]?', text)
        if like_match:
            col = like_match.group(1)
            pattern = like_match.group(2)
            conditions.append(f"{col} LIKE '{pattern}'")

        # 4. Standard comparisons (with explicit field)
        patterns = [
            (r"(\w+)\s+is\s+['\"]?([^'\"]+)['\"]?", r"\1 = '\2'"),
            (r"(\w+)\s+equal[s]?\s+to\s+['\"]?([^'\"]+)['\"]?", r"\1 = '\2'"),
            (r"(\w+)\s+greater\s+than\s+(\d+)", r"\1 > \2"),
            (r"(\w+)\s+less\s+than\s+(\d+)", r"\1 < \2"),
            (r"(\w+)\s+older\s+than\s+(\d+)", r"age > \2"),
            (r"(\w+)\s+younger\s+than\s+(\d+)", r"age < \2"),
            (r"(\w+)\s+with\s+id\s+(\d+)", r"id = \2"),
            (r"id\s+(\d+)", r"id = \1"),
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

        # Combine with AND (we ignore OR for simplicity)
        if conditions:
            return " AND ".join(conditions)
        return None

    # ---------- JOIN detection ----------
    def _detect_join(self, text: str, table: str) -> Optional[Tuple[str, str, str]]:
        """Return (join_type, other_table, condition) if a join is detected."""
        # Look for phrases like "users and orders" or "users join orders"
        tables = self._get_known_tables()
        other_table = None
        for t in tables:
            if t != table and re.search(rf'\b{t}\b', text):
                other_table = t
                break
        if not other_table:
            return None
        # Determine join type
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
        else:
            join_type = "INNER"
        # Guess join condition (assume table.id = other_table.table_id or similar)
        # Try to find explicit condition like "on table.id = other.id"
        on_match = re.search(r'on\s+([\w.]+)\s*=\s*([\w.]+)', text)
        if on_match:
            cond = f"{on_match.group(1)} = {on_match.group(2)}"
        else:
            # Default guess: primary key of first table is id, foreign key in second table is table_id
            cond = f"{table}.id = {other_table}.{table}_id"
        return (join_type, other_table, cond)

    # ---------- Subquery detection ----------
    def _detect_subquery(self, text: str, main_table: str) -> Optional[Tuple[str, str, str]]:
        """Detect subquery patterns like 'users who bought' -> IN (SELECT user_id FROM orders)"""
        # Look for "who bought" or "who purchased"
        if re.search(r'\b(?:who|that)\s+(?:bought|purchased|ordered)', text):
            # Assume subquery on orders table
            return ("IN", "id", f"SELECT user_id FROM orders")
        # Generic: try to find a table that is not the main table and not already joined
        tables = self._get_known_tables()
        for t in tables:
            if t != main_table and re.search(rf'\b{t}\b', text):
                # Guess a simple subquery
                return ("IN", "id", f"SELECT {main_table}_id FROM {t}")
        return None

    # ---------- Aggregation detection ----------
    def _extract_aggregates(self, text: str) -> List[Tuple[str, str, str]]:
        """Return list of (function, column, alias)."""
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
                alias = f"{col}_{func.lower()}"
                aggregates.append((func, col, alias))
        # Also check for "*" count
        if "count of all" in text or "count of rows" in text:
            aggregates.append(("COUNT", "*", "total"))
        return aggregates

    def _extract_group_by(self, text: str) -> Optional[str]:
        group_match = re.search(r"group\s+by\s+(\w+)", text)
        if group_match:
            return group_match.group(1)
        return None

    def _extract_having(self, text: str) -> Optional[str]:
        having_match = re.search(r"having\s+(.+?)(?:\s+order\s+by|\s+limit|$)", text)
        if having_match:
            cond = having_match.group(1).strip()
            # Convert to SQL condition (simplistic)
            cond = re.sub(r'([a-z_]+)\s+is\s+', r'\1 = ', cond)
            return cond
        return None

    def _extract_order_by(self, text: str) -> Optional[Tuple[str, str]]:
        order_match = re.search(r"order\s+by\s+(\w+)\s*(asc|desc)?", text)
        if order_match:
            col = order_match.group(1)
            direction = order_match.group(2).upper() if order_match.group(2) else "ASC"
            return (col, direction)
        return None

    def _extract_limit(self, text: str) -> Optional[int]:
        limit_match = re.search(r"limit\s+(\d+)", text)
        if limit_match:
            return int(limit_match.group(1))
        return None

    def _extract_offset(self, text: str) -> Optional[int]:
        offset_match = re.search(r"offset\s+(\d+)", text)
        return int(offset_match.group(1)) if offset_match else None

    # ---------- NoSQL specific operators ----------
    def _detect_nosql_operator(self, text: str) -> Optional[Tuple[str, str, Any]]:
        """For UPDATE, detect PUSH, PULL, ADDTOSET."""
        for op in ["push", "pull", "addtoset"]:
            match = re.search(rf"{op}\s+['\"]?(\w+)['\"]?", text)
            if match:
                value = match.group(1)
                # Try to parse as number if possible
                try:
                    if value.isdigit():
                        value = int(value)
                except:
                    pass
                return (op.upper(), value)
        return None

    def _detect_contains(self, text: str) -> Optional[Tuple[str, str]]:
        """For NoSQL WHERE, detect CONTAINS."""
        match = re.search(r"contains\s+['\"]?(\w+)['\"]?", text)
        if match:
            return ("CONTAINS", match.group(1))
        return None

    # ---------- DDL operations ----------
    def _generate_create_index(self, text: str) -> Optional[str]:
        # CREATE INDEX idx_name ON table (column)
        match = re.search(r"create\s+index\s+(\w+)\s+on\s+(\w+)\s*\(?\s*(\w+)\s*\)?", text)
        if match:
            idx_name = match.group(1)
            table = match.group(2)
            col = match.group(3)
            # Determine SQL vs NoSQL
            if table in self._get_known_tables():
                # Check if it's a SQL table
                try:
                    sql_tables = list(self.catalog.get_current_sql_engine().storage.data["tables"].keys())
                    if table in sql_tables:
                        return f"SQL CREATE INDEX {idx_name} ON {table} ({col})"
                except:
                    pass
                return f"NOSQL CREATE INDEX {idx_name} ON {table} ({col})"
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
        # Detect various ALTER operations
        # ADD COLUMN
        add_match = re.search(r"add\s+column\s+(\w+)\s+(\w+)", text)
        if add_match:
            col = add_match.group(1)
            typ = add_match.group(2).upper()
            table = self._extract_table(text)
            if table:
                return f"SQL ALTER TABLE {table} ADD {col} {typ}"
        # RENAME COLUMN
        rename_match = re.search(r"rename\s+column\s+(\w+)\s+to\s+(\w+)", text)
        if rename_match:
            old = rename_match.group(1)
            new = rename_match.group(2)
            table = self._extract_table(text)
            if table:
                return f"SQL ALTER TABLE {table} RENAME COLUMN {old} TO {new}"
        # DROP COLUMN
        drop_match = re.search(r"drop\s+column\s+(\w+)", text)
        if drop_match:
            col = drop_match.group(1)
            table = self._extract_table(text)
            if table:
                return f"SQL ALTER TABLE {table} DROP COLUMN {col}"
        # RENAME TABLE
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
        # Simplified: CREATE TRIGGER name BEFORE|AFTER INSERT|UPDATE|DELETE ON table FOR EACH ROW BEGIN ... END
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

    # ---------- SELECT generation (enhanced) ----------
    def _generate_select(self, text: str) -> str:
        table = self._extract_table(text) or "users"
        # Determine SQL vs NoSQL
        sql_tables = []
        try:
            sql_tables = list(self.catalog.get_current_sql_engine().storage.data["tables"].keys())
        except:
            pass
        prefix = "SQL" if table in sql_tables else "NOSQL"

        # Check for join
        join_info = self._detect_join(text, table)
        if join_info:
            join_type, other_table, join_cond = join_info
            # For simplicity, we'll generate a SELECT with the join
            # The FROM clause will include the join
            from_clause = f"{table} {join_type} JOIN {other_table} ON {join_cond}"
            # We'll reconstruct the whole command
            command = f"{prefix} SELECT * FROM {from_clause}"
            # Add WHERE, ORDER BY, LIMIT later if needed
            where_cond = self._extract_conditions(text)
            if where_cond:
                command += f" WHERE {where_cond}"
            order_by = self._extract_order_by(text)
            if order_by:
                col, direction = order_by
                command += f" ORDER BY {col} {direction}"
            limit = self._extract_limit(text)
            if limit:
                command += f" LIMIT {limit}"
            return command

        # Check for subquery
        subquery_info = self._detect_subquery(text, table)
        if subquery_info:
            sub_type, outer_col, inner_sql = subquery_info
            if sub_type == "IN":
                where_cond = f"{outer_col} IN ({inner_sql})"
                command = f"{prefix} SELECT * FROM {table} WHERE {where_cond}"
                # Add other clauses
                order_by = self._extract_order_by(text)
                if order_by:
                    col, direction = order_by
                    command += f" ORDER BY {col} {direction}"
                limit = self._extract_limit(text)
                if limit:
                    command += f" LIMIT {limit}"
                return command

        # Regular SELECT
        # Extract columns
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

        # Extract aggregates (multiple)
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
            # No aggregates but GROUP BY – we need to include group_by column
            if columns:
                select_clause = ", ".join(columns)
            else:
                select_clause = group_by

        # Build command
        command = f"{prefix} SELECT {select_clause} FROM {table}"

        # WHERE
        where_cond = self._extract_conditions(text)
        if where_cond:
            command += f" WHERE {where_cond}"

        # GROUP BY
        if group_by:
            command += f" GROUP BY {group_by}"
            # HAVING
            having = self._extract_having(text)
            if having:
                command += f" HAVING {having}"

        # ORDER BY
        order_by = self._extract_order_by(text)
        if order_by:
            col, direction = order_by
            command += f" ORDER BY {col} {direction}"

        # LIMIT
        limit = self._extract_limit(text)
        offset = self._extract_offset(text)
        if limit:
            if offset:
                command += f" LIMIT {offset}, {limit}"
            else:
                command += f" LIMIT {limit}"

        return command

    # ---------- INSERT generation (enhanced) ----------
    def _generate_insert(self, text: str) -> str:
        table = self._extract_table(text) or "users"
        # Try to extract column-value pairs from phrases like "name Alice age 30"
        pairs = {}
        # Look for "name = value" or "name is value"
        for match in re.finditer(r'(\w+)\s+(?:is|=)\s+[\'"]?(\w+)[\'"]?', text):
            col, val = match.group(1), match.group(2)
            pairs[col] = val
        # Also look for "named Alice age 30"
        name_match = re.search(r'named?\s+[\'"]?(\w+)[\'"]?', text)
        if name_match and "name" not in pairs:
            pairs["name"] = name_match.group(1)
        age_match = re.search(r'age\s+(\d+)', text)
        if age_match and "age" not in pairs:
            pairs["age"] = age_match.group(1)
        if pairs:
            cols = list(pairs.keys())
            vals = [f"'{v}'" if not v.isdigit() else v for v in pairs.values()]
            return f"SQL INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)})"
        else:
            return f"SQL INSERT INTO {table} DEFAULT VALUES"

    # ---------- UPDATE generation (enhanced) ----------
    def _generate_update(self, text: str) -> str:
        table = self._extract_table(text) or "users"
        # Detect NoSQL operator (PUSH, PULL, ADDTOSET)
        nosql_op = self._detect_nosql_operator(text)
        if nosql_op:
            op, val = nosql_op
            # Determine which field to update
            field_match = re.search(r'(\w+)\s+(?:push|pull|addtoset)', text)
            if field_match:
                field = field_match.group(1)
                condition = self._extract_conditions(text)
                condition_part = f" WHERE {condition}" if condition else ""
                return f"NOSQL UPDATE {table} SET {field} {op} {val}{condition_part}"
            else:
                return f"NOSQL UPDATE {table}"
        # Normal SQL update
        set_pairs = []
        # Look for "set age 31" or "age = 31"
        set_matches = re.finditer(r'(\w+)\s+(?:to|=)?\s+[\'"]?(\w+)[\'"]?', text)
        for match in set_matches:
            col, val = match.group(1), match.group(2)
            if col in ["name", "age", "id", "status"]:  # common columns
                set_pairs.append(f"{col} = '{val}'" if not val.isdigit() else f"{col} = {val}")
        if set_pairs:
            condition = self._extract_conditions(text)
            condition_part = f" WHERE {condition}" if condition else ""
            return f"SQL UPDATE {table} SET {', '.join(set_pairs)}{condition_part}"
        return f"SQL UPDATE {table}"

    # ---------- DELETE generation (enhanced) ----------
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