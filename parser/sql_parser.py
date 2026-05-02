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