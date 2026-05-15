# backend/parser/sql_parser.py
import re
from typing import Dict, Any, List, Tuple, Optional

class SQLParser:
    @staticmethod
    def parse_create_table(sql: str) -> Dict[str, Any]:
        pattern = r"CREATE\s+TABLE\s+(\w+)\s*\((.*)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE TABLE syntax")
        table_name = match.group(1)
        columns_def = match.group(2).strip()
        columns = []
        for col_str in columns_def.split(','):
            col_str = col_str.strip()
            if not col_str:
                continue
            parts = col_str.split()
            if len(parts) < 2:
                raise ValueError(f"Invalid column definition: '{col_str}'")
            col_name = parts[0]
            col_type = parts[1].upper()
            constraints = [c.upper() for c in parts[2:]]
            if "PRIMARY" in constraints and "KEY" in constraints:
                constraints.remove("PRIMARY")
                constraints.remove("KEY")
                constraints.append("PRIMARY KEY")
            if "NOT" in constraints and "NULL" in constraints:
                constraints.remove("NOT")
                constraints.remove("NULL")
                constraints.append("NOT NULL")
            columns.append({
                "name": col_name,
                "type": col_type,
                "constraints": constraints
            })
        return {"table_name": table_name, "columns": columns}

    @staticmethod
    def parse_insert(sql: str) -> Dict[str, Any]:
        """
        Parses:
          INSERT INTO table_name VALUES (val1, val2, ...)
          INSERT INTO table_name (col1, col2, ...) VALUES (val1, val2, ...)
        Returns dict with:
          table_name, columns (list of column names, empty if all), values (list of raw strings)
        """
        sql = sql.strip()
        pattern = r"INSERT\s+INTO\s+(\w+)\s*(?:\(([^)]+)\))?\s+VALUES\s*\((.+)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid INSERT syntax. Expected: INSERT INTO table (columns) VALUES (values) or INSERT INTO table VALUES (values)")
        table_name = match.group(1)
        columns_str = match.group(2)
        values_str = match.group(3)
        columns = []
        if columns_str:
            columns = [c.strip() for c in columns_str.split(',')]
        values = []
        current = ""
        in_quote = False
        for ch in values_str:
            if ch == "'" and not in_quote:
                in_quote = True
                current += ch
            elif ch == "'" and in_quote:
                in_quote = False
                current += ch
            elif ch == ',' and not in_quote:
                values.append(current.strip())
                current = ""
            else:
                current += ch
        if current:
            values.append(current.strip())
        cleaned_values = []
        for v in values:
            v = v.strip()
            if v.startswith("'") and v.endswith("'"):
                cleaned_values.append(v[1:-1])
            elif v.upper() == "NULL":
                cleaned_values.append(None)
            elif v.isdigit():
                cleaned_values.append(int(v))
            else:
                try:
                    cleaned_values.append(float(v))
                except:
                    cleaned_values.append(v)
        return {
            "table_name": table_name,
            "columns": columns,
            "values": cleaned_values
        }

    @staticmethod
    def parse_select(sql: str) -> Dict[str, Any]:
        """Parse SELECT with support for joins, subqueries, and cross‑model NOSQL subqueries."""
        sql = sql.strip()
        # Find the position of the main FROM keyword (not inside parentheses)
        paren_depth = 0
        from_pos = -1
        for i, ch in enumerate(sql):
            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth -= 1
            elif paren_depth == 0 and sql[i:i+4].upper() == 'FROM':
                from_pos = i
                break
        if from_pos == -1:
            raise ValueError("SELECT missing FROM clause")
        select_part = sql[6:from_pos].strip()        # after "SELECT "
        after_from = sql[from_pos+4:].strip()        # everything after FROM

        # --- Find where the FROM clause ends (before WHERE, ORDER, GROUP, LIMIT, HAVING) ---
        # Scan until we hit one of these keywords (not inside parentheses)
        clause_keywords = ['WHERE', 'ORDER BY', 'GROUP BY', 'LIMIT', 'HAVING']
        clause_positions = {}
        for kw in clause_keywords:
            depth = 0
            kw_len = len(kw)
            for j, ch in enumerate(after_from):
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                elif depth == 0 and after_from[j:j+kw_len].upper() == kw:
                    clause_positions[kw] = j
                    break
        # Earliest clause start
        from_end = len(after_from)
        if clause_positions:
            from_end = min(clause_positions.values())
        from_clause = after_from[:from_end].strip()
        rest = after_from[from_end:].strip() if from_end < len(after_from) else ""

        # --- Tokenize the FROM clause (respect parentheses for subqueries) ---
        tokens = []
        current = []
        in_paren = 0
        for ch in from_clause:
            if ch == '(':
                in_paren += 1
            elif ch == ')':
                in_paren -= 1
            elif ch.isspace() and in_paren == 0:
                if current:
                    tokens.append(''.join(current))
                    current = []
                continue
            current.append(ch)
        if current:
            tokens.append(''.join(current))

        if not tokens:
            raise ValueError("Invalid FROM clause")
        main_table = tokens[0]
        joins = []
        i = 1
        while i < len(tokens):
            token = tokens[i].upper()
            if token == 'INNER':
                join_type = 'INNER'
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after INNER JOIN")
                join_table = tokens[i]
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'ON':
                    raise ValueError(f"Missing ON clause for JOIN {join_table}")
                i += 1
                condition_parts = []
                while i < len(tokens) and tokens[i].upper() not in ['INNER', 'LEFT', 'RIGHT', 'FULL', 'JOIN']:
                    condition_parts.append(tokens[i])
                    i += 1
                condition = ' '.join(condition_parts)
                joins.append(('INNER', join_table, condition))
            elif token == 'LEFT':
                join_type = 'LEFT'
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'JOIN':
                    raise ValueError("Expected JOIN after LEFT")
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after LEFT JOIN")
                join_table = tokens[i]
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'ON':
                    raise ValueError("Missing ON clause for LEFT JOIN")
                i += 1
                condition_parts = []
                while i < len(tokens) and tokens[i].upper() not in ['INNER', 'LEFT', 'RIGHT', 'FULL', 'JOIN']:
                    condition_parts.append(tokens[i])
                    i += 1
                condition = ' '.join(condition_parts)
                joins.append(('LEFT', join_table, condition))
            elif token == 'RIGHT':
                join_type = 'RIGHT'
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'JOIN':
                    raise ValueError("Expected JOIN after RIGHT")
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after RIGHT JOIN")
                join_table = tokens[i]
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'ON':
                    raise ValueError("Missing ON clause for RIGHT JOIN")
                i += 1
                condition_parts = []
                while i < len(tokens) and tokens[i].upper() not in ['INNER', 'LEFT', 'RIGHT', 'FULL', 'JOIN']:
                    condition_parts.append(tokens[i])
                    i += 1
                condition = ' '.join(condition_parts)
                joins.append(('RIGHT', join_table, condition))
            elif token == 'FULL':
                join_type = 'FULL'
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'JOIN':
                    raise ValueError("Expected JOIN after FULL")
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after FULL JOIN")
                join_table = tokens[i]
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'ON':
                    raise ValueError("Missing ON clause for FULL JOIN")
                i += 1
                condition_parts = []
                while i < len(tokens) and tokens[i].upper() not in ['INNER', 'LEFT', 'RIGHT', 'FULL', 'JOIN']:
                    condition_parts.append(tokens[i])
                    i += 1
                condition = ' '.join(condition_parts)
                joins.append(('FULL', join_table, condition))
            elif token == 'NATURAL':
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'JOIN':
                    raise ValueError("Expected JOIN after NATURAL")
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after NATURAL JOIN")
                join_table = tokens[i]
                i += 1
                joins.append(('NATURAL', join_table, None))
            elif token == 'CROSS':
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'JOIN':
                    raise ValueError("Expected JOIN after CROSS")
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after CROSS JOIN")
                join_table = tokens[i]
                i += 1
                joins.append(('CROSS', join_table, None))
            elif token == 'JOIN':
                join_type = 'INNER'
                join_table = tokens[i+1] if i+1 < len(tokens) else None
                if not join_table:
                    raise ValueError("Missing table after JOIN")
                i += 2
                if i >= len(tokens) or tokens[i].upper() != 'ON':
                    raise ValueError("Missing ON clause for JOIN")
                i += 1
                condition_parts = []
                while i < len(tokens) and tokens[i].upper() not in ['INNER', 'LEFT', 'RIGHT', 'FULL', 'JOIN']:
                    condition_parts.append(tokens[i])
                    i += 1
                condition = ' '.join(condition_parts)
                joins.append(('INNER', join_table, condition))
            else:
                # If we encounter something unexpected, it might be part of a subquery inside the FROM clause?
                # But for cross-model subqueries, the WHERE clause is isolated above.
                raise ValueError(f"Unexpected token in FROM clause: {token}")

        # --- Parse SELECT columns ---
        if select_part == "*":
            columns = "*"
        else:
            columns = []
            current = []
            paren_depth = 0
            for ch in select_part:
                if ch == '(':
                    paren_depth += 1
                elif ch == ')':
                    paren_depth -= 1
                elif ch == ',' and paren_depth == 0:
                    columns.append(''.join(current).strip())
                    current = []
                    continue
                current.append(ch)
            if current:
                columns.append(''.join(current).strip())
            columns = [c for c in columns if c]

        # --- Parse WHERE, ORDER BY, LIMIT, GROUP BY, HAVING from the remaining part (rest) ---
        where_cond = None
        order_by = None
        limit = None
        group_by = None
        having = None

        # WHERE
        where_match = re.search(r"\bWHERE\s+(.*?)(?:\s+ORDER\s+BY|\s+GROUP\s+BY|\s+LIMIT|$)", rest, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_cond = where_match.group(1).strip()
        # ORDER BY
        order_match = re.search(r"\bORDER\s+BY\s+(\w+)\s*(ASC|DESC)?", rest, re.IGNORECASE)
        if order_match:
            col = order_match.group(1)
            direction = order_match.group(2).upper() if order_match.group(2) else "ASC"
            order_by = (col, direction)
        # LIMIT
        limit_match = re.search(r"\bLIMIT\s+(\d+)(?:\s*,\s*(\d+))?", rest, re.IGNORECASE)
        if limit_match:
            if limit_match.group(2) is not None:
                offset = int(limit_match.group(1))
                limit_val = int(limit_match.group(2))
                limit = (limit_val, offset)
            else:
                limit_val = int(limit_match.group(1))
                limit = (limit_val, None)
        # GROUP BY
        group_match = re.search(r"\bGROUP\s+BY\s+(\w+)", rest, re.IGNORECASE)
        if group_match:
            group_by = group_match.group(1)
        # HAVING
        if group_by:
            having_match = re.search(r"\bHAVING\s+(.*?)(?:\s+ORDER\s+BY|\s+LIMIT|$)", rest, re.IGNORECASE | re.DOTALL)
            if having_match:
                having = having_match.group(1).strip()

        return {
            "columns": columns,
            "main_table": main_table,
            "joins": joins,
            "where": where_cond,
            "order_by": order_by,
            "limit": limit,
            "group_by": group_by,
            "having": having
        }

    @staticmethod
    def parse_update(sql: str) -> Dict[str, Any]:
        """
        Parses: UPDATE table_name SET col1 = val1, col2 = val2 [WHERE condition]
        """
        sql = sql.strip()
        pattern = r"UPDATE\s+(\w+)\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?$"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid UPDATE syntax. Expected: UPDATE table SET col=val, ... [WHERE condition]")
        table_name = match.group(1)
        set_part = match.group(2).strip()
        where_cond = match.group(3).strip() if match.group(3) else None
        assignments = {}
        parts = set_part.split(',')
        for part in parts:
            if '=' not in part:
                raise ValueError(f"Invalid SET clause: '{part}'")
            col, val_str = part.split('=', 1)
            col = col.strip()
            val_str = val_str.strip()
            if val_str.upper() == "NULL":
                assignments[col] = None
            elif val_str.startswith("'") and val_str.endswith("'"):
                assignments[col] = val_str[1:-1]
            elif val_str.isdigit():
                assignments[col] = int(val_str)
            else:
                try:
                    assignments[col] = float(val_str)
                except:
                    assignments[col] = val_str
        return {"table": table_name, "set": assignments, "where": where_cond}

    @staticmethod
    def parse_delete(sql: str) -> Dict[str, Any]:
        """
        Parses: DELETE FROM table_name [WHERE condition]
        """
        sql = sql.strip()
        pattern = r"DELETE\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?$"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DELETE syntax. Expected: DELETE FROM table [WHERE condition]")
        table_name = match.group(1)
        where_cond = match.group(2).strip() if match.group(2) else None
        return {"table": table_name, "where": where_cond}

    @staticmethod
    def parse_drop_table(sql: str) -> str:
        """Parses: DROP TABLE table_name, returns table name."""
        sql = sql.strip()
        pattern = r"DROP\s+TABLE\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP TABLE syntax. Expected: DROP TABLE table_name")
        return match.group(1)

    @staticmethod
    def extract_subqueries(where_clause: str) -> List[Tuple[str, str]]:
        """
        Find all subqueries in a WHERE clause.
        Returns list of (full_match, subquery_sql) for IN and EXISTS.
        """
        if not where_clause:
            return []
        subqueries = []
        in_pattern = r"(\w+)\s+IN\s*\(\s*(SELECT .+?)\s*\)"
        for match in re.finditer(in_pattern, where_clause, re.IGNORECASE):
            column = match.group(1)
            sub_sql = match.group(2)
            subqueries.append(("IN", column, sub_sql))
        exists_pattern = r"EXISTS\s*\(\s*(SELECT .+?)\s*\)"
        for match in re.finditer(exists_pattern, where_clause, re.IGNORECASE):
            sub_sql = match.group(1)
            subqueries.append(("EXISTS", None, sub_sql))
        return subqueries