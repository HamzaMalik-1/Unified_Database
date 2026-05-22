# backend/parser/nosql_parser.py
import re
import json
from typing import Dict, Any, List, Optional, Tuple

class NoSQLParser:
    @staticmethod
    def parse_create_collection(sql: str) -> str:
        """Extract collection name from CREATE COLLECTION name"""
        pattern = r"CREATE\s+COLLECTION\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE COLLECTION syntax")
        return match.group(1)

    @staticmethod
    def parse_insert(sql: str) -> Dict[str, Any]:
        """
        Parses: INSERT INTO collection (key1, key2, ...) VALUES (val1, val2, ...)
        Values may be JSON objects (e.g., {"city":"Boston"}) or primitives.
        Returns dict: {"collection": str, "doc": dict}
        """
        pattern = r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)\s+VALUES\s*\((.+)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid INSERT syntax for NoSQL")
        collection = match.group(1)
        keys_str = match.group(2).strip()
        values_str = match.group(3).strip()
        keys = [k.strip() for k in keys_str.split(',')]
        # Parse values – need to handle JSON objects and strings
        values = NoSQLParser._parse_value_list(values_str)
        if len(keys) != len(values):
            raise ValueError(f"Number of keys ({len(keys)}) does not match number of values ({len(values)})")
        doc = {}
        for k, v in zip(keys, values):
            doc[k] = v
        return {"collection": collection, "doc": doc}

    @staticmethod
    def parse_select(sql: str) -> Dict[str, Any]:
        """
        Parses NoSQL SELECT with full SQL-like syntax:
            SELECT [* | field1, field2, ...] FROM collection
            [WHERE condition]
            [ORDER BY field [ASC|DESC]]
            [LIMIT [offset,] limit]
            [GROUP BY field]
            [HAVING condition]
        Returns dict with keys: columns, collection, where, order_by, limit, group_by, having
        """
        sql = sql.strip()
        # Extract FROM part
        from_match = re.search(r"\bFROM\s+(\w+)", sql, re.IGNORECASE)
        if not from_match:
            raise ValueError("SELECT missing FROM clause")
        collection = from_match.group(1)

        # Extract columns (between SELECT and FROM)
        select_to_from = re.match(r"SELECT\s+(.+?)\s+FROM", sql, re.IGNORECASE | re.DOTALL)
        if not select_to_from:
            raise ValueError("Invalid SELECT syntax")
        columns_part = select_to_from.group(1).strip()
        if columns_part == "*":
            columns = "*"
        else:
            columns = [c.strip() for c in columns_part.split(',')]

        # WHERE clause
        where_cond = None
        where_match = re.search(r"\bWHERE\s+(.+?)(?:\s+ORDER\s+BY|\s+GROUP\s+BY|\s+LIMIT|$)", sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_cond = where_match.group(1).strip()

        # ORDER BY
        order_by = None
        order_match = re.search(r"\bORDER\s+BY\s+([\w.]+)\s*(ASC|DESC)?", sql, re.IGNORECASE)
        if order_match:
            field = order_match.group(1)
            direction = order_match.group(2).upper() if order_match.group(2) else "ASC"
            order_by = (field, direction)

        # LIMIT
        limit = None
        limit_match = re.search(r"\bLIMIT\s+(\d+)(?:\s*,\s*(\d+))?", sql, re.IGNORECASE)
        if limit_match:
            if limit_match.group(2) is not None:
                offset = int(limit_match.group(1))
                limit_val = int(limit_match.group(2))
                limit = (limit_val, offset)   # (limit, offset)
            else:
                limit_val = int(limit_match.group(1))
                limit = (limit_val, None)      # (limit, no offset)

        # GROUP BY
        group_by = None
        group_match = re.search(r"\bGROUP\s+BY\s+([\w.]+)", sql, re.IGNORECASE)
        if group_match:
            group_by = group_match.group(1)

        # HAVING (only if GROUP BY exists)
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
        """
        Parses: UPDATE collection SET field1 = val1, field2 = val2 [WHERE condition]
        WHERE condition can be a simple field = value (dot notation supported).
        Returns dict: {
            "collection": str,
            "set": List[Tuple[str, Any]],   # list of (field, value)
            "where": Optional[Tuple[str, str, Any]]   # (field, operator, value) or None
        }
        """
        sql = sql.strip()
        # First, extract SET part and optional WHERE
        # Pattern: UPDATE collection SET ... [WHERE ...]
        set_match = re.match(r"UPDATE\s+(\w+)\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?$", sql, re.IGNORECASE)
        if not set_match:
            raise ValueError("Invalid UPDATE syntax. Expected: UPDATE collection SET field = value, ... [WHERE condition]")
        collection = set_match.group(1)
        set_clause = set_match.group(2).strip()
        where_clause = set_match.group(3).strip() if set_match.group(3) else None

        # Parse SET assignments (comma separated, but values may contain commas? For simplicity, split by comma not inside quotes)
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
            if '=' not in assign:
                raise ValueError(f"Invalid SET assignment: {assign}")
            field, val_str = assign.split('=', 1)
            field = field.strip()
            val_str = val_str.strip()
            # Parse value
            val = NoSQLParser._parse_value(val_str)
            set_pairs.append((field, val))

        # Parse WHERE clause (if present)
        where = None
        if where_clause:
            # Support simple condition: field op value (with =, !=, >, <, >=, <=)
            where_match = re.match(r'^([\w.]+)\s*([=<>!]+)\s*(.+)$', where_clause.strip())
            if not where_match:
                raise ValueError("Invalid WHERE condition. Expected: field operator value")
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
        """
        Parses: DELETE FROM collection WHERE field = value
        Returns dict: {"collection": str, "where_field": str, "where_value": any}
        If no WHERE, delete all documents.
        """
        pattern = r"DELETE\s+FROM\s+(\w+)(?:\s+WHERE\s+([\w.]+)\s*=\s*(.+))?$"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DELETE syntax. Expected: DELETE FROM collection [WHERE field = value]")
        collection = match.group(1)
        if match.group(2) and match.group(3):
            where_field = match.group(2).strip()
            where_val_str = match.group(3).strip()
            where_value = NoSQLParser._parse_value(where_val_str)
            return {"collection": collection, "where_field": where_field, "where_value": where_value}
        else:
            return {"collection": collection, "where_field": None, "where_value": None}

    # Helper methods
    @staticmethod
    def _parse_value_list(values_str: str) -> List[Any]:
        """Parse comma-separated values, where each can be a JSON object, string, number, or NULL."""
        values = []
        current = ""
        in_quote = False
        in_object = 0
        i = 0
        while i < len(values_str):
            ch = values_str[i]
            if ch == "'" and not in_object:
                in_quote = not in_quote
                current += ch
            elif ch == '{' and not in_quote:
                in_object += 1
                current += ch
            elif ch == '}' and not in_quote:
                in_object -= 1
                current += ch
            elif ch == ',' and not in_quote and in_object == 0:
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
        if val_str.startswith("{") and val_str.endswith("}"):
            # Parse JSON object
            try:
                # Replace single quotes with double quotes for json.loads (only if not inside strings)
                import json
                # For simplicity, assume the user provides valid JSON with double quotes.
                # But our examples use single quotes. We'll convert.
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
    @staticmethod
    def parse_drop_collection(sql: str) -> str:
        """Extract collection name from DROP COLLECTION name"""
        pattern = r"DROP\s+COLLECTION\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP COLLECTION syntax. Expected: DROP COLLECTION collection_name")
        return match.group(1)
        
    @staticmethod
    def parse_create_index(sql: str) -> Dict[str, Any]:
        pattern = r"CREATE\s+INDEX\s+(\w+)\s+ON\s+(\w+)\s*\(\s*([\w.]+)\s*\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE INDEX syntax. Expected: CREATE INDEX idx_name ON collection (field)")
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
            raise ValueError("Invalid DROP INDEX syntax. Expected: DROP INDEX index_name")
        return match.group(1)
    
    @staticmethod
    def parse_create_text_index(sql: str) -> Dict[str, Any]:
        """Parses: CREATE TEXT INDEX idx_name ON collection (field)"""
        pattern = r"CREATE\s+TEXT\s+INDEX\s+(\w+)\s+ON\s+(\w+)\s*\(\s*([\w.]+)\s*\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE TEXT INDEX syntax. Expected: CREATE TEXT INDEX idx_name ON collection (field)")
        return {
            "index_name": match.group(1),
            "collection": match.group(2),
            "field": match.group(3)
        }

    @staticmethod
    def parse_drop_text_index(sql: str) -> str:
        """Parses: DROP TEXT INDEX idx_name"""
        pattern = r"DROP\s+TEXT\s+INDEX\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP TEXT INDEX syntax. Expected: DROP TEXT INDEX index_name")
        return match.group(1)