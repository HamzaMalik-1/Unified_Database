# backend/parser/nosql_parser.py
import re
import json
from typing import Dict, Any, List, Optional, Tuple

class NoSQLParser:
    @staticmethod
    def parse_create_collection(sql: str) -> str:
        pattern = r"CREATE\s+COLLECTION\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE COLLECTION syntax")
        return match.group(1)

    @staticmethod
    def parse_insert(sql: str) -> Dict[str, Any]:
        pattern = r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)\s+VALUES\s*\((.+)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid INSERT syntax for NoSQL")
        collection = match.group(1)
        keys_str = match.group(2).strip()
        values_str = match.group(3).strip()
        keys = [k.strip() for k in keys_str.split(',')]
        values = NoSQLParser._parse_value_list(values_str)
        if len(keys) != len(values):
            raise ValueError(f"Number of keys ({len(keys)}) does not match number of values ({len(values)})")
        doc = {}
        for k, v in zip(keys, values):
            doc[k] = v
        return {"collection": collection, "doc": doc}

    @staticmethod
    def parse_select(sql: str) -> Dict[str, Any]:
        sql = sql.strip()
        from_match = re.search(r"\bFROM\s+(\w+)", sql, re.IGNORECASE)
        if not from_match:
            raise ValueError("SELECT missing FROM clause")
        collection = from_match.group(1)
        select_to_from = re.match(r"SELECT\s+(.+?)\s+FROM", sql, re.IGNORECASE | re.DOTALL)
        if not select_to_from:
            raise ValueError("Invalid SELECT syntax")
        columns_part = select_to_from.group(1).strip()
        columns = "*" if columns_part == "*" else [c.strip() for c in columns_part.split(',')]
        where_cond = None
        where_match = re.search(r"\bWHERE\s+(.+?)(?:\s+ORDER\s+BY|\s+GROUP\s+BY|\s+LIMIT|$)", sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_cond = where_match.group(1).strip()
        order_by = None
        order_match = re.search(r"\bORDER\s+BY\s+([\w.]+)\s*(ASC|DESC)?", sql, re.IGNORECASE)
        if order_match:
            field = order_match.group(1)
            direction = order_match.group(2).upper() if order_match.group(2) else "ASC"
            order_by = (field, direction)
        limit = None
        limit_match = re.search(r"\bLIMIT\s+(\d+)(?:\s*,\s*(\d+))?", sql, re.IGNORECASE)
        if limit_match:
            if limit_match.group(2) is not None:
                offset = int(limit_match.group(1))
                limit_val = int(limit_match.group(2))
                limit = (limit_val, offset)
            else:
                limit_val = int(limit_match.group(1))
                limit = (limit_val, None)
        group_by = None
        group_match = re.search(r"\bGROUP\s+BY\s+([\w.]+)", sql, re.IGNORECASE)
        if group_match:
            group_by = group_match.group(1)
        having = None
        if group_by:
            having_match = re.search(r"\bHAVING\s+(.+?)(?:\s+ORDER\s+BY|\s+LIMIT|$)", sql, re.IGNORECASE | re.DOTALL)
            if having_match:
                having = having_match.group(1).strip()
        return {
            "columns": columns,
            "collection": collection,
            "where": where_cond,
            "order_by": order_by,
            "limit": limit,
            "group_by": group_by,
            "having": having
        }

    @staticmethod
    def parse_update(sql: str) -> Dict[str, Any]:
        sql = sql.strip()
        set_match = re.match(r"UPDATE\s+(\w+)\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?$", sql, re.IGNORECASE)
        if not set_match:
            raise ValueError("Invalid UPDATE syntax")
        collection = set_match.group(1)
        set_clause = set_match.group(2).strip()
        where_clause = set_match.group(3).strip() if set_match.group(3) else None

        # Parse SET assignments (supports both "field = value", "field PUSH value", etc.)
        assignments = []
        current = ""
        in_quote = False
        for ch in set_clause:
            if ch == "'" and not in_quote:
                in_quote = True
                current += ch
            elif ch == "'" and in_quote:
                in_quote = False
                current += ch
            elif ch == ',' and not in_quote:
                assignments.append(current.strip())
                current = ""
            else:
                current += ch
        if current:
            assignments.append(current.strip())

        set_pairs = []
        for assign in assignments:
            # Try to detect array operators: field PUSH value, field PULL value, field ADDTOSET value
            op_match = re.match(r'^([\w.]+)\s+(PUSH|PULL|ADDTOSET)\s+(.+)$', assign, re.IGNORECASE)
            if op_match:
                field = op_match.group(1).strip()
                op = op_match.group(2).upper()
                val_str = op_match.group(3).strip()
                val = NoSQLParser._parse_value(val_str)
                set_pairs.append((field, op, val))
            else:
                # Normal assignment: field = value
                if '=' not in assign:
                    raise ValueError(f"Invalid SET assignment: {assign}")
                field, val_str = assign.split('=', 1)
                field = field.strip()
                val_str = val_str.strip()
                val = NoSQLParser._parse_value(val_str)
                set_pairs.append((field, "=", val))

        # Parse WHERE clause (unchanged)
        where = None
        if where_clause:
            where_match = re.match(r'^([\w.]+)\s*([=<>!]+)\s*(.+)$', where_clause.strip())
            if not where_match:
                raise ValueError("Invalid WHERE condition")
            field = where_match.group(1).strip()
            operator = where_match.group(2).strip()
            val_str = where_match.group(3).strip()
            val = NoSQLParser._parse_value(val_str)
            where = (field, operator, val)

        return {
            "collection": collection,
            "set": set_pairs,
            "where": where
        }

    @staticmethod
    def parse_delete(sql: str) -> Dict[str, Any]:
        pattern = r"DELETE\s+FROM\s+(\w+)(?:\s+WHERE\s+([\w.]+)\s*=\s*(.+))?$"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DELETE syntax")
        collection = match.group(1)
        if match.group(2) and match.group(3):
            where_field = match.group(2).strip()
            where_val_str = match.group(3).strip()
            where_value = NoSQLParser._parse_value(where_val_str)
            return {"collection": collection, "where_field": where_field, "where_value": where_value}
        else:
            return {"collection": collection, "where_field": None, "where_value": None}

    # ---------- Helper methods ----------
    @staticmethod
    def _parse_value_list(values_str: str) -> List[Any]:
        """Parse comma-separated values, where each can be a JSON object, array, string, number, or NULL."""
        values = []
        current = ""
        in_quote = False
        in_object = 0
        in_array = 0
        i = 0
        while i < len(values_str):
            ch = values_str[i]
            if ch == "'" and not in_object and not in_array:
                in_quote = not in_quote
                current += ch
            elif ch == '[' and not in_quote and not in_object:
                in_array += 1
                current += ch
            elif ch == ']' and not in_quote and not in_object:
                in_array -= 1
                current += ch
            elif ch == '{' and not in_quote and not in_array:
                in_object += 1
                current += ch
            elif ch == '}' and not in_quote and not in_array:
                in_object -= 1
                current += ch
            elif ch == ',' and not in_quote and in_object == 0 and in_array == 0:
                current = current.strip()
                values.append(NoSQLParser._parse_value(current))
                current = ""
            else:
                current += ch
            i += 1
        if current.strip():
            values.append(NoSQLParser._parse_value(current.strip()))
        return values

    @staticmethod
    def _parse_value(val_str: str) -> Any:
        val_str = val_str.strip()
        if val_str.upper() == "NULL":
            return None
        if val_str.startswith("'") and val_str.endswith("'"):
            return val_str[1:-1]
        if val_str.startswith("[") and val_str.endswith("]"):
            # Parse JSON array (simple: replace single quotes with double quotes for json.loads)
            try:
                arr_str = val_str.replace("'", '"')
                return json.loads(arr_str)
            except:
                raise ValueError(f"Invalid JSON array: {val_str}")
        if val_str.startswith("{") and val_str.endswith("}"):
            # Parse JSON object
            try:
                json_str = val_str.replace("'", '"')
                return json.loads(json_str)
            except:
                raise ValueError(f"Invalid JSON object: {val_str}")
        if val_str.isdigit():
            return int(val_str)
        try:
            return float(val_str)
        except:
            return val_str

    # ---------- Additional commands ----------
    @staticmethod
    def parse_drop_collection(sql: str) -> str:
        pattern = r"DROP\s+COLLECTION\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP COLLECTION syntax")
        return match.group(1)

    @staticmethod
    def parse_create_index(sql: str) -> Dict[str, Any]:
        pattern = r"CREATE\s+INDEX\s+(\w+)\s+ON\s+(\w+)\s*\(\s*([\w.]+)\s*\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE INDEX syntax")
        return {
            "index_name": match.group(1),
            "collection": match.group(2),
            "field": match.group(3)
        }

    @staticmethod
    def parse_drop_index(sql: str) -> str:
        pattern = r"DROP\s+INDEX\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP INDEX syntax")
        return match.group(1)

    @staticmethod
    def parse_create_text_index(sql: str) -> Dict[str, Any]:
        pattern = r"CREATE\s+TEXT\s+INDEX\s+(\w+)\s+ON\s+(\w+)\s*\(\s*([\w.]+)\s*\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE TEXT INDEX syntax")
        return {
            "index_name": match.group(1),
            "collection": match.group(2),
            "field": match.group(3)
        }

    @staticmethod
    def parse_drop_text_index(sql: str) -> str:
        pattern = r"DROP\s+TEXT\s+INDEX\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP TEXT INDEX syntax")
        return match.group(1)