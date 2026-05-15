# backend/nlp_processor.py
import re
from typing import Optional, List
from word2number import w2n

class NLPProcessor:
    def __init__(self, catalog):
        self.catalog = catalog

    def process(self, text: str) -> Optional[str]:
        text = text.lower().strip()
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
            if t.lower() in text:
                return t
        return None

    def _extract_condition(self, text: str) -> Optional[str]:
        # Special handling for "with id" pattern
        # Example: "delete user with id 2" -> field="id", value=2
        id_match = re.search(r"with\s+id\s+(\d+)", text)
        if id_match:
            value = id_match.group(1)
            # Convert word numbers if any
            try:
                value = str(w2n.word_to_num(value))
            except:
                pass
            return f"id = {value}"

        # Other patterns: field op value
        patterns = [
            (r"(\w+)\s+is\s+['\"]?([^'\"]+)['\"]?", ("=", 1, 2)),
            (r"(\w+)\s+equal[s]?\s+to\s+['\"]?([^'\"]+)['\"]?", ("=", 1, 2)),
            (r"(\w+)\s+greater\s+than\s+(\d+)", (">", 1, 2)),
            (r"(\w+)\s+less\s+than\s+(\d+)", ("<", 1, 2)),
            (r"(\w+)\s+older\s+than\s+(\d+)", (">", 1, 2)),
            (r"(\w+)\s+younger\s+than\s+(\d+)", ("<", 1, 2)),
            (r"id\s+(\d+)", ("=", None, 1)),
        ]
        for pattern, (op, field_group, val_group) in patterns:
            match = re.search(pattern, text)
            if match:
                if field_group is not None:
                    field = match.group(field_group)
                else:
                    field = "id"
                val = match.group(val_group)
                try:
                    val = str(w2n.word_to_num(val))
                except:
                    pass
                if not val.isdigit() and not val.startswith("'") and not val.startswith('"'):
                    val = f"'{val}'"
                return f"{field} {op} {val}"
        return None

    def _generate_select(self, text: str) -> str:
        table = self._extract_table(text) or "users"
        condition = self._extract_condition(text)
        sql_tables = []
        try:
            sql_tables = list(self.catalog.get_current_sql_engine().storage.data["tables"].keys())
        except:
            pass
        prefix = "SQL" if table in sql_tables else "NOSQL"
        if condition:
            return f"{prefix} SELECT * FROM {table} WHERE {condition}"
        else:
            return f"{prefix} SELECT * FROM {table}"

    def _generate_insert(self, text: str) -> str:
        table = self._extract_table(text) or "users"
        name_match = re.search(r"named?\s+['\"]?(\w+)['\"]?", text)
        name = name_match.group(1) if name_match else None
        age_match = re.search(r"age\s+(\d+)", text)
        age = age_match.group(1) if age_match else None
        if name and age:
            return f"SQL INSERT INTO {table} (name, age) VALUES ('{name}', {age})"
        elif name:
            return f"SQL INSERT INTO {table} (name) VALUES ('{name}')"
        else:
            return f"SQL INSERT INTO {table} DEFAULT VALUES"

    def _generate_update(self, text: str) -> str:
        table = self._extract_table(text) or "users"
        condition = self._extract_condition(text)
        set_match = re.search(r"(age|name)\s+(\w+)", text)
        if set_match:
            set_col = set_match.group(1)
            set_val = set_match.group(2)
            if condition:
                return f"SQL UPDATE {table} SET {set_col} = '{set_val}' WHERE {condition}"
        return f"SQL UPDATE {table}"

    def _generate_delete(self, text: str) -> str:
        table = self._extract_table(text) or "users"
        condition = self._extract_condition(text)
        if condition:
            return f"SQL DELETE FROM {table} WHERE {condition}"
        else:
            return f"SQL DELETE FROM {table}"

    def _generate_create_table(self, text: str) -> str:
        match = re.search(r"create\s+table\s+(\w+)", text)
        if match:
            table = match.group(1)
            return f"SQL CREATE TABLE {table} (id INT PRIMARY KEY, name TEXT)"
        return None

    def _generate_create_collection(self, text: str) -> str:
        match = re.search(r"create\s+collection\s+(\w+)", text)
        if match:
            collection = match.group(1)
            return f"NOSQL CREATE COLLECTION {collection}"
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