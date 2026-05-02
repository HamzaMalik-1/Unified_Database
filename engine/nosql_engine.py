# backend/engine/nosql_engine.py
from storage.storage import Storage
from parser.nosql_parser import NoSQLParser
from loguru import logger
from typing import Dict, Any, List,Tuple
import json


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
        elif cmd_upper.startswith("SELECT "):
            return self._select(command)  
        elif cmd_upper.startswith("UPDATE "):
            # Check if it's UPDATE (without IN) – we need to distinguish from UPDATE IN (old)
            # We'll call a unified method
            return self._update(command)
        elif cmd_upper.startswith("DROP COLLECTION"):
            return self._drop_collection(command)
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
        try:
            parsed = NoSQLParser.parse_select(sql)
            collection = parsed["collection"]
            columns_spec = parsed["columns"]
            where_cond = parsed["where"]
            order_by = parsed["order_by"]
            limit = parsed["limit"]
            group_by = parsed["group_by"]
            having_cond = parsed["having"]

            if collection not in self.storage.data['collections']:
                return f"Error: Collection '{collection}' does not exist"
            docs = self.storage.data['collections'][collection].copy()

            # Apply WHERE
            if where_cond:
                docs = [d for d in docs if self._evaluate_condition(d, where_cond)]

            # GROUP BY + aggregates
            if group_by:
                # Determine aggregates and regular fields
                if columns_spec == "*":
                    # For SELECT *, we treat all fields as regular (no aggregates)
                    # But with GROUP BY, we need to pick one representative per group? Better to require explicit aggregates.
                    regular_fields = []
                    agg_specs = {}
                else:
                    regular_fields, agg_specs = self._prepare_aggregates(columns_spec)

                # Group documents by group_by field (dot notation supported)
                groups = {}
                for doc in docs:
                    key = self._get_nested(doc, group_by)
                    if key not in groups:
                        groups[key] = []
                    groups[key].append(doc)

                result_docs = []
                for key, group_docs in groups.items():
                    row = {group_by: key}
                    # For regular fields, take the first non-null value (arbitrary)
                    for f in regular_fields:
                        val = None
                        for d in group_docs:
                            val = self._get_nested(d, f)
                            if val is not None:
                                break
                        row[f] = val
                    # Compute aggregates
                    for alias, (func, src_field) in agg_specs.items():
                        if src_field == "count":
                            values = [1] * len(group_docs)
                        else:
                            values = [self._get_nested(d, src_field) for d in group_docs if self._get_nested(d, src_field) is not None]
                        if func == "COUNT":
                            result = len(group_docs)
                        elif func == "SUM":
                            result = sum(values) if values else 0
                        elif func == "AVG":
                            result = sum(values) / len(values) if values else 0
                        elif func == "MIN":
                            result = min(values) if values else None
                        elif func == "MAX":
                            result = max(values) if values else None
                        row[alias] = result
                    result_docs.append(row)
                docs = result_docs

                # Apply HAVING
                if having_cond:
                    docs = [d for d in docs if self._evaluate_condition(d, having_cond)]

            else:
                # No GROUP BY – if aggregates are present, compute over all docs
                if columns_spec != "*":
                    regular_fields, agg_specs = self._prepare_aggregates(columns_spec)
                    if agg_specs:
                        # Single document result
                        row = {}
                        for alias, (func, src_field) in agg_specs.items():
                            if src_field == "count":
                                values = [1] * len(docs)
                            else:
                                values = [self._get_nested(d, src_field) for d in docs if self._get_nested(d, src_field) is not None]
                            if func == "COUNT":
                                row[alias] = len(docs)
                            elif func == "SUM":
                                row[alias] = sum(values) if values else 0
                            elif func == "AVG":
                                row[alias] = sum(values) / len(values) if values else 0
                            elif func == "MIN":
                                row[alias] = min(values) if values else None
                            elif func == "MAX":
                                row[alias] = max(values) if values else None
                        # For regular fields, we cannot include them without GROUP BY – ignore
                        docs = [row]
                    else:
                        # No aggregates, just select specific fields later
                        pass

            # Apply ORDER BY (after grouping and before limit)
            if order_by:
                field, direction = order_by
                reverse = (direction.upper() == "DESC")
                docs.sort(key=lambda d: self._get_nested(d, field), reverse=reverse)

            # Apply LIMIT
            if limit:
                limit_val, offset = limit
                start = offset if offset is not None else 0
                docs = docs[start:start+limit_val]

            # Select specific columns (if not already handled by grouping/aggregates)
            if columns_spec != "*" and not group_by and not agg_specs:
                # Project only specified fields
                selected = []
                for doc in docs:
                    new_doc = {}
                    for col in columns_spec:
                        new_doc[col] = self._get_nested(doc, col)
                    selected.append(new_doc)
                docs = selected
            elif columns_spec != "*" and group_by:
                # Already built result rows (they have the aliased columns)
                # No further projection needed
                pass
            elif columns_spec == "*":
                # Keep all fields
                pass

            return json.dumps(docs, indent=2, default=str)

        except Exception as e:
            logger.error(f"NoSQL SELECT failed: {e}")
            return f"Error: {e}"

    def _update(self, sql: str) -> str:
        """Handles UPDATE collection SET field = value, ... [WHERE condition]"""
        try:
            parsed = NoSQLParser.parse_update(sql)
            coll = parsed["collection"]
            set_pairs = parsed["set"]
            where = parsed["where"]

            if coll not in self.storage.data['collections']:
                return f"Error: Collection '{coll}' does not exist"
            docs = self.storage.data['collections'][coll]
            updated = 0
            for doc in docs:
                # Check WHERE condition
                if where is not None:
                    field, operator, value = where
                    left_val = self._get_nested(doc, field)
                    # Evaluate condition
                    if operator == "=" or operator == "==":
                        if not (left_val == value):
                            continue
                    elif operator == "!=":
                        if not (left_val != value):
                            continue
                    elif operator == ">":
                        if not (left_val > value):
                            continue
                    elif operator == "<":
                        if not (left_val < value):
                            continue
                    elif operator == ">=":
                        if not (left_val >= value):
                            continue
                    elif operator == "<=":
                        if not (left_val <= value):
                            continue
                    else:
                        continue
                # Apply SET assignments
                for field, val in set_pairs:
                    self._set_nested(doc, field, val)
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
                # Delete all documents
                count = len(docs)
                self.storage.data['collections'][coll] = []
                self.storage.save()
                return f"Delete successful. {count} document(s) deleted."
            else:
                # Delete matching documents
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
        
    def _evaluate_condition(self, doc: Dict, condition_str: str) -> bool:
        """
        Evaluate simple WHERE condition like:
            address.city = 'New York'
            age > 25
            name = 'Alice'
        Supports =, >, <, >=, <=, !=
        """
        if not condition_str:
            return True
        cond = condition_str.strip()
        # Split into left part, operator, right part
        import re
        # Match patterns: field op value (op can be =, !=, >, <, >=, <=)
        match = re.match(r'^([\w.]+)\s*([=<>!]+)\s*(.+)$', cond)
        if not match:
            # Fallback to eval (maybe complex condition with AND/OR) but we don't support for now
            return False
        field = match.group(1).strip()
        operator = match.group(2).strip()
        right_raw = match.group(3).strip()

        # Parse right value (strip quotes, convert numbers)
        if right_raw.startswith("'") and right_raw.endswith("'"):
            right_val = right_raw[1:-1]
        elif right_raw.isdigit():
            right_val = int(right_raw)
        else:
            try:
                right_val = float(right_raw)
            except:
                right_val = right_raw

        # Get left value from document using nested access
        left_val = self._get_nested(doc, field)

        # Compare based on operator
        if operator == "=" or operator == "==":
            return left_val == right_val
        elif operator == "!=":
            return left_val != right_val
        elif operator == ">":
            return left_val > right_val
        elif operator == "<":
            return left_val < right_val
        elif operator == ">=":
            return left_val >= right_val
        elif operator == "<=":
            return left_val <= right_val
        else:
            return False

    def _prepare_aggregates(self, columns: List[str]) -> Tuple[List[str], Dict[str, Tuple[str, str]]]:
        """
        Parse column list, detect aggregates.
        Returns (regular_columns, aggregate_specs)
        aggregate_specs: {alias: (function, source_field)}
        """
        import re
        regular = []
        aggregates = {}
        agg_patterns = {
            "COUNT": r"COUNT\(\s*(?:\*|\w+)\s*\)",
            "SUM": r"SUM\(\s*([\w.]+)\s*\)",
            "AVG": r"AVG\(\s*([\w.]+)\s*\)",
            "MIN": r"MIN\(\s*([\w.]+)\s*\)",
            "MAX": r"MAX\(\s*([\w.]+)\s*\)"
        }
        for col in columns:
            matched = False
            for func, pattern in agg_patterns.items():
                match = re.match(pattern, col, re.IGNORECASE)
                if match:
                    if func == "COUNT":
                        inner = "count"
                    else:
                        inner = match.group(1)
                    alias_match = re.search(r"\s+AS\s+(\w+)", col, re.IGNORECASE)
                    alias = alias_match.group(1) if alias_match else f"{func}({inner})"
                    aggregates[alias] = (func, inner)
                    matched = True
                    break
            if not matched:
                regular.append(col)
        return regular, aggregates
    def _flatten_dict(self, d: Dict, parent_key: str = '', sep: str = '.') -> Dict:
        """Convert nested dict into flat dict with dot‑notation keys."""
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)
    
    def _drop_collection(self, sql: str) -> str:
        try:
            name = NoSQLParser.parse_drop_collection(sql)
            if name not in self.storage.data['collections']:
                return f"Error: Collection '{name}' does not exist"
            del self.storage.data['collections'][name]
            self.storage.save()
            logger.info(f"Dropped collection '{name}'")
            return f"Collection '{name}' dropped successfully"
        except Exception as e:
            logger.error(f"DROP COLLECTION failed: {e}")
            return f"Error: {e}"