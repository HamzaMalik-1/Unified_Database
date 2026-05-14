# backend/parser/sql_parser.py
import re
from typing import Dict, Any, List, Tuple, Optional


class SQLParser:
    @staticmethod
    def parse_create_table(sql: str) -> Dict[str, Any]:
        # existing code (unchanged)
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
        # Pattern for INSERT INTO table (optional columns) VALUES (...)
        # Capture: table name, optional column list, values list
        pattern = r"INSERT\s+INTO\s+(\w+)\s*(?:\(([^)]+)\))?\s+VALUES\s*\((.+)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid INSERT syntax. Expected: INSERT INTO table (columns) VALUES (values) or INSERT INTO table VALUES (values)")
        table_name = match.group(1)
        columns_str = match.group(2)  # may be None
        values_str = match.group(3)
        # Parse columns
        columns = []
        if columns_str:
            columns = [c.strip() for c in columns_str.split(',')]
        # Parse values - split by comma but ignore commas inside quotes
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
        # Clean values: remove surrounding quotes and convert numbers
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
            "columns": columns,   # empty list if not specified (means all columns)
            "values": cleaned_values
        }
        
    @staticmethod
    def parse_select(sql: str) -> Dict[str, Any]:
        """
        Parses SELECT statements with:
          - column list (list of strings, or '*' for all)
          - FROM table
          - WHERE condition (optional)
          - ORDER BY column [ASC|DESC] (optional)
          - LIMIT [offset,] limit (optional)
          - GROUP BY column (optional)
          - HAVING condition (optional, requires GROUP BY)
        Returns dict: {
            "columns": List[str] or "*",
            "table": str,
            "where": Optional[str],
            "order_by": Optional[Tuple[str, str]],  # (column, direction)
            "limit": Optional[Tuple[int, Optional[int]]],  # (limit, offset) or (limit, None) or (limit, offset)
            "group_by": Optional[str],
            "having": Optional[str]
        }
        """
        sql = sql.strip()
        # Normalize to uppercase for easier keyword detection
        sql_upper = sql.upper()
        # We'll use regex with case-insensitive but preserve original for column names

        # Extract FROM part
        from_match = re.search(r"\bFROM\s+(\w+)", sql, re.IGNORECASE)
        if not from_match:
            raise ValueError("SELECT missing FROM clause")
        table_name = from_match.group(1)

        # Extract columns part (between SELECT and FROM)
        select_to_from = re.match(r"SELECT\s+(.+?)\s+FROM", sql, re.IGNORECASE)
        if not select_to_from:
            raise ValueError("Invalid SELECT syntax")
        columns_part = select_to_from.group(1).strip()
        if columns_part == "*":
            columns = "*"
        else:
            # Split by comma but ignore commas inside functions (e.g., COUNT(*))
            # Simple split for now; assuming no commas inside functions.
            columns = [c.strip() for c in columns_part.split(',')]

        # Extract WHERE clause (everything after WHERE up to next keyword)
        where_clause = None
        where_match = re.search(r"\bWHERE\s+(.*?)(?:\s+ORDER\s+BY|\s+GROUP\s+BY|\s+LIMIT|$)", sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_clause = where_match.group(1).strip()

        # Extract ORDER BY
        order_by = None
        order_match = re.search(r"\bORDER\s+BY\s+(\w+)\s*(ASC|DESC)?", sql, re.IGNORECASE)
        if order_match:
            col = order_match.group(1)
            dir = order_match.group(2).upper() if order_match.group(2) else "ASC"
            order_by = (col, dir)

        # Extract LIMIT (supports: LIMIT n, LIMIT offset, limit)
        limit = None
        # Pattern for LIMIT offset, limit or LIMIT limit
        limit_match = re.search(r"\bLIMIT\s+(\d+)(?:\s*,\s*(\d+))?", sql, re.IGNORECASE)
        if limit_match:
            if limit_match.group(2) is not None:
                offset = int(limit_match.group(1))
                limit_val = int(limit_match.group(2))
                limit = (limit_val, offset)
            else:
                limit_val = int(limit_match.group(1))
                limit = (limit_val, None)  # no offset

        # Extract GROUP BY (simple, single column)
        group_by = None
        group_match = re.search(r"\bGROUP\s+BY\s+(\w+)", sql, re.IGNORECASE)
        if group_match:
            group_by = group_match.group(1)

        # Extract HAVING (only if GROUP BY exists)
        having = None
        if group_by:
            having_match = re.search(r"\bHAVING\s+(.+?)(?:\s+ORDER\s+BY|\s+LIMIT|\s*$)", sql, re.IGNORECASE | re.DOTALL)
            if having_match:
                having = having_match.group(1).strip()

        return {
            "columns": columns,
            "table": table_name,
            "where": where_clause,
            "order_by": order_by,
            "limit": limit,
            "group_by": group_by,
            "having": having
        }
        
        
        
        
    @staticmethod
    def parse_update(sql: str) -> Dict[str, Any]:
        """
        Parses: UPDATE table_name SET col1 = val1, col2 = val2 [WHERE condition]
        Returns dict: {"table": str, "set": Dict[str, Any], "where": Optional[str]}
        """
        sql = sql.strip()
        # Pattern: UPDATE table SET assignments [WHERE condition]
        pattern = r"UPDATE\s+(\w+)\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?$"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid UPDATE syntax. Expected: UPDATE table SET col=val, ... [WHERE condition]")
        table_name = match.group(1)
        set_part = match.group(2).strip()
        where_cond = match.group(3).strip() if match.group(3) else None

        # Parse assignments: col = value, col = value
        assignments = {}
        parts = set_part.split(',')
        for part in parts:
            if '=' not in part:
                raise ValueError(f"Invalid SET clause: '{part}'")
            col, val_str = part.split('=', 1)
            col = col.strip()
            val_str = val_str.strip()
            # Convert value to appropriate type
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
        Returns dict: {"table": str, "where": Optional[str]}
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
        Example: "id IN (SELECT user_id FROM orders)" 
        -> [("id IN (SELECT user_id FROM orders)", "SELECT user_id FROM orders")]
        """
        if not where_clause:
            return []
        subqueries = []
        # Pattern for IN (SELECT ...)
        in_pattern = r"(\w+)\s+IN\s*\(\s*(SELECT .+?)\s*\)"
        for match in re.finditer(in_pattern, where_clause, re.IGNORECASE):
            column = match.group(1)
            sub_sql = match.group(2)
            subqueries.append(("IN", column, sub_sql))
        # Pattern for EXISTS (SELECT ...)
        exists_pattern = r"EXISTS\s*\(\s*(SELECT .+?)\s*\)"
        for match in re.finditer(exists_pattern, where_clause, re.IGNORECASE):
            sub_sql = match.group(1)
            subqueries.append(("EXISTS", None, sub_sql))
        return subqueries