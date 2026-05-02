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
        Parses: SELECT FROM collection WHERE field = value
        (supports dot notation in field)
        """
        pattern = r"SELECT\s+FROM\s+(\w+)\s+WHERE\s+([\w.]+)\s*=\s*(.+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            # Fallback to old FIND syntax
            return NoSQLParser.parse_find(sql)
        collection = match.group(1)
        field = match.group(2).strip()
        val_str = match.group(3).strip()
        value = NoSQLParser._parse_value(val_str)
        return {"collection": collection, "field": field, "value": value, "operator": "="}

    @staticmethod
    def parse_update(sql: str) -> Dict[str, Any]:
        """
        Parses: UPDATE collection SET field = value WHERE field = value
        (no 'IN')
        """
        pattern = r"UPDATE\s+(\w+)\s+SET\s+([\w.]+)\s*=\s*(.+?)\s+WHERE\s+([\w.]+)\s*=\s*(.+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            # Fallback to old UPDATE IN syntax
            return NoSQLParser.parse_update(sql)
        collection = match.group(1)
        set_field = match.group(2).strip()
        set_val_str = match.group(3).strip()
        where_field = match.group(4).strip()
        where_val_str = match.group(5).strip()
        return {
            "collection": collection,
            "set_field": set_field,
            "set_value": NoSQLParser._parse_value(set_val_str),
            "where_field": where_field,
            "where_value": NoSQLParser._parse_value(where_val_str)
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