# backend/engine/nosql_engine.py
from storage.storage import Storage
from parser.nosql_parser import NoSQLParser
from loguru import logger
from typing import Dict, Any, List

class NoSQLDatabase:
    def __init__(self, storage: Storage):
        self.storage = storage
        # Ensure structure: storage.data["collections"] = dict of collection name -> list of docs
        if 'collections' not in self.storage.data:
            self.storage.data['collections'] = {}
            self.storage.save()

    def execute(self, command: str) -> str:
        cmd_upper = command.strip().upper()
        if cmd_upper.startswith("CREATE COLLECTION"):
            return self._create_collection(command)
        elif cmd_upper.startswith("INSERT INTO"):
            return self._insert(command)
        elif cmd_upper.startswith("SELECT FROM"):
            return self._select(command)  
        elif cmd_upper.startswith("UPDATE "):
            # Check if it's UPDATE (without IN) – we need to distinguish from UPDATE IN (old)
            # We'll call a unified method
            return self._update(command)
        elif cmd_upper.startswith("DELETE FROM"):
            return self._delete(command)
        else:
            return f"Unsupported NoSQL command: {command}"

    def _create_collection(self, sql: str) -> str:
        try:
            name = NoSQLParser.parse_create_collection(sql)
            if name in self.storage.data['collections']:
                return f"Collection '{name}' already exists"
            self.storage.data['collections'][name] = []
            self.storage.save()
            logger.info(f"Created collection '{name}'")
            return f"Collection '{name}' created"
        except Exception as e:
            logger.error(f"CREATE COLLECTION failed: {e}")
            return f"Error: {e}"

    def _insert(self, sql: str) -> str:
        try:
            parsed = NoSQLParser.parse_insert(sql)
            coll = parsed["collection"]
            doc = parsed["doc"]
            if coll not in self.storage.data['collections']:
                return f"Error: Collection '{coll}' does not exist. Create it first."
            self.storage.data['collections'][coll].append(doc)
            self.storage.save()
            logger.info(f"Inserted document into '{coll}': {doc}")
            return "Insert successful"
        except Exception as e:
            logger.error(f"INSERT failed: {e}")
            return f"Error: {e}"

    def _select(self, sql: str) -> str:
        """Handles SELECT FROM collection WHERE field = value (dot notation supported)"""
        try:
            parsed = NoSQLParser.parse_select(sql)
            coll = parsed["collection"]
            field = parsed["field"]
            value = parsed["value"]
            if coll not in self.storage.data['collections']:
                return f"Error: Collection '{coll}' does not exist"
            results = []
            for doc in self.storage.data['collections'][coll]:
                if self._get_nested(doc, field) == value:
                    results.append(doc)
            import json
            return json.dumps(results, indent=2, default=str)
        except Exception as e:
            logger.error(f"SELECT failed: {e}")
            return f"Error: {e}"

    def _update(self, sql: str) -> str:
        """Handles UPDATE collection SET field = value WHERE field = value (no 'IN')"""
        try:
            parsed = NoSQLParser.parse_update(sql)
            coll = parsed["collection"]
            set_field = parsed["set_field"]
            set_value = parsed["set_value"]
            where_field = parsed["where_field"]
            where_value = parsed["where_value"]
            if coll not in self.storage.data['collections']:
                return f"Error: Collection '{coll}' does not exist"
            updated = 0
            for doc in self.storage.data['collections'][coll]:
                if self._get_nested(doc, where_field) == where_value:
                    self._set_nested(doc, set_field, set_value)
                    updated += 1
            self.storage.save()
            return f"Update successful. {updated} document(s) updated."
        except Exception as e:
            logger.error(f"UPDATE failed: {e}")
            return f"Error: {e}"

    def _delete(self, sql: str) -> str:
        try:
            parsed = NoSQLParser.parse_delete(sql)
            coll = parsed["collection"]
            where_field = parsed["where_field"]
            where_value = parsed["where_value"]
            if coll not in self.storage.data['collections']:
                return f"Error: Collection '{coll}' does not exist"
            docs = self.storage.data['collections'][coll]
            if where_field is None:
                # Delete all
                count = len(docs)
                self.storage.data['collections'][coll] = []
                self.storage.save()
                return f"Delete successful. {count} document(s) deleted."
            else:
                new_docs = []
                deleted = 0
                for doc in docs:
                    doc_val = self._get_nested(doc, where_field)
                    if doc_val == where_value:
                        deleted += 1
                    else:
                        new_docs.append(doc)
                self.storage.data['collections'][coll] = new_docs
                self.storage.save()
                return f"Delete successful. {deleted} document(s) deleted."
        except Exception as e:
            logger.error(f"DELETE failed: {e}")
            return f"Error: {e}"

    # Helper functions for nested field access
    def _get_nested(self, doc: Dict, path: str) -> Any:
        """Retrieve value from nested dict using dot notation, e.g., 'address.city'"""
        parts = path.split('.')
        current = doc
        for p in parts:
            if isinstance(current, dict) and p in current:
                current = current[p]
            else:
                return None
        return current

    def _set_nested(self, doc: Dict, path: str, value: Any) -> None:
        """Set value in nested dict using dot notation, creating intermediate dicts as needed."""
        parts = path.split('.')
        current = doc
        for i, p in enumerate(parts[:-1]):
            if p not in current or not isinstance(current[p], dict):
                current[p] = {}
            current = current[p]
        current[parts[-1]] = value