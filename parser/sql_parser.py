import re
from typing import Dict, Any

class SQLParser:
    @staticmethod
    def parse_create_table(sql: str) -> Dict[str, Any]:
        """
        Parses: CREATE TABLE table_name ( col_name type [constraints], ... )
        Returns dict with 'table_name' and 'columns' list.
        Each column: {'name': str, 'type': str, 'constraints': list of str}
        """
        pattern = r"CREATE\s+TABLE\s+(\w+)\s*\((.*)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE TABLE syntax. Expected: CREATE TABLE name (col1 type, ...)")
        table_name = match.group(1)
        columns_def = match.group(2).strip()
        columns = []
        # Split by comma not inside parentheses? For now assume simple.
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
            # Handle PRIMARY KEY as two words: PRIMARY and KEY -> merge into one constraint
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