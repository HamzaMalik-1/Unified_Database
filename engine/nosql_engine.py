# backend/engine/nosql_engine.py
from storage.storage import Storage
from parser.nosql_parser import NoSQLParser
from loguru import logger
from typing import Dict, Any, List, Tuple, Optional
import json
import re
import copy
from pathlib import Path
from utils.response import ResponseFactory, APIResponse
from transaction.log_manager import LogManager   # new import

class NoSQLDatabase:
    def __init__(self, storage: Storage):
        self.storage = storage
        if 'collections' not in self.storage.data:
            self.storage.data['collections'] = {}
        if 'nosql_indexes' not in self.storage.data:
            self.storage.data['nosql_indexes'] = {}
        if 'text_indexes' not in self.storage.data:
            self.storage.data['text_indexes'] = {}
        self.storage.save()

        # Transaction attributes
        self.active_txn = None          # current txn_id (int) or None
        self.txn_snapshot = None        # deep copy of storage.data at BEGIN
        self.log_path = Path("data/nosql_wal.log")
        self.log = None                 # LogManager instance (created in begin)

        # Recover from WAL on startup
        self._recover_from_wal()

    # ------------------------------------------------------------------
    # Transaction support (mirroring SQL engine)
    # ------------------------------------------------------------------
    def begin(self, txn_id: int = None):
        if self.active_txn is not None:
            raise Exception("Transaction already active")
        if txn_id is None:
            txn_id = self._next_txn_id()
        self.active_txn = txn_id
        self.txn_snapshot = copy.deepcopy(self.storage.data)
        self.log = LogManager(self.log_path)
        self.log.write({"txn_id": txn_id, "op": "BEGIN"})

    def commit(self):
        if self.active_txn is None:
            raise Exception("No active transaction")
        self.log.write({"txn_id": self.active_txn, "op": "COMMIT"})
        self.log.flush()
        self.storage.save()   # persist all changes
        self._clear_transaction()

    def rollback(self):
        if self.active_txn is None:
            raise Exception("No active transaction")
        # Restore snapshot
        self.storage.data = self.txn_snapshot
        self.log.write({"txn_id": self.active_txn, "op": "ROLLBACK"})
        self.log.flush()
        self._clear_transaction()

    def _clear_transaction(self):
        self.active_txn = None
        self.txn_snapshot = None
        if self.log:
            self.log.close()
            self.log = None

    def _next_txn_id(self) -> int:
        import time
        return int(time.time() * 1000)

    # ------------------------------------------------------------------
    # Crash recovery from WAL
    # ------------------------------------------------------------------
    def _recover_from_wal(self):
        if not self.log_path.exists():
            return

        committed_txns = set()
        records = []

        # First pass: read all records, mark committed transactions
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                records.append(rec)
                if rec.get("op") == "COMMIT":
                    committed_txns.add(rec["txn_id"])

        # Second pass: replay INSERT/UPDATE/DELETE from committed txns
        for rec in records:
            txn_id = rec.get("txn_id")
            if txn_id not in committed_txns:
                continue
            op = rec.get("op")
            if op == "INSERT":
                self._apply_insert_recovery(rec["collection"], rec["doc"])
            elif op == "UPDATE":
                self._apply_update_recovery(rec["collection"], rec["new"], rec.get("old"))
            elif op == "DELETE":
                self._apply_delete_recovery(rec["collection"], rec["old"])

    def _get_doc_id(self, doc: dict) -> Any:
        """Extract unique identifier from a document (prefer '_id' or 'id', else use whole doc as fallback)."""
        if "_id" in doc:
            return doc["_id"]
        if "id" in doc:
            return doc["id"]
        # Fallback: use a hash of the document (acceptable for lab project)
        return hash(json.dumps(doc, sort_keys=True))

    def _apply_insert_recovery(self, collection: str, doc: dict):
        docs = self.storage.data['collections'].get(collection, [])
        doc_id = self._get_doc_id(doc)
        # Avoid duplicate if already present
        for existing in docs:
            if self._get_doc_id(existing) == doc_id:
                return
        docs.append(doc)
        self.storage.data['collections'][collection] = docs
        # Indexes will be rebuilt later (or we can update lazily). For simplicity, we just restore data.

    def _apply_update_recovery(self, collection: str, new_doc: dict, old_doc: dict = None):
        docs = self.storage.data['collections'].get(collection, [])
        doc_id = self._get_doc_id(new_doc)
        for i, existing in enumerate(docs):
            if self._get_doc_id(existing) == doc_id:
                docs[i] = new_doc
                break
        self.storage.data['collections'][collection] = docs

    def _apply_delete_recovery(self, collection: str, old_doc: dict):
        docs = self.storage.data['collections'].get(collection, [])
        doc_id = self._get_doc_id(old_doc)
        new_docs = [d for d in docs if self._get_doc_id(d) != doc_id]
        self.storage.data['collections'][collection] = new_docs

    # ---------- Main execute method (unchanged) ----------
    def execute(self, command: str) -> APIResponse:
        cmd_upper = command.strip().upper()
        try:
            if cmd_upper.startswith("CREATE COLLECTION"):
                result = self._create_collection(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("INSERT INTO"):
                result = self._insert(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("SELECT "):
                result_str = self._select(command)
                if result_str.startswith("Error:"):
                    return ResponseFactory.error(result_str, status=400)
                try:
                    data = json.loads(result_str)
                    if isinstance(data, list):
                        has_nested = any(isinstance(v, dict) for doc in data if isinstance(doc, dict) for v in doc.values())
                        if has_nested:
                            return ResponseFactory.json(data, message="Query executed successfully")
                        else:
                            if data and all(isinstance(item, dict) for item in data):
                                return ResponseFactory.json(data, message="Query executed successfully")
                            else:
                                return ResponseFactory.json(data, message="Query executed successfully")
                    else:
                        return ResponseFactory.json(data, message="Query executed successfully")
                except Exception:
                    return ResponseFactory.text(result_str, message="Query executed successfully")
            elif cmd_upper.startswith("UPDATE "):
                result = self._update(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("DROP COLLECTION"):
                result = self._drop_collection(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("DELETE FROM"):
                result = self._delete(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("CREATE INDEX"):
                result = self._create_index(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("DROP INDEX"):
                result = self._drop_index(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("CREATE TEXT INDEX"):
                result = self._create_text_index(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("DROP TEXT INDEX"):
                result = self._drop_text_index(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            else:
                return ResponseFactory.error(f"Unsupported NoSQL command: {command}", status=400)
        except Exception as e:
            logger.error(f"NoSQL execution failed: {e}")
            return ResponseFactory.error(str(e), status=500)

    # ---------- Collection management (auto‑commit) ----------
    def _create_collection(self, sql: str) -> str:
        try:
            name = NoSQLParser.parse_create_collection(sql)
            if name in self.storage.data['collections']:
                return f"Collection '{name}' already exists"
            self.storage.data['collections'][name] = []
            # Only save immediately if not in a transaction (auto‑commit)
            if not self.active_txn:
                self.storage.save()
            logger.info(f"Created collection '{name}'")
            return f"Collection '{name}' created"
        except Exception as e:
            logger.error(f"CREATE COLLECTION failed: {e}")
            return f"Error: {e}"

    def _drop_collection(self, sql: str) -> str:
        try:
            name = NoSQLParser.parse_drop_collection(sql)
            if name not in self.storage.data['collections']:
                return f"Error: Collection '{name}' does not exist"
            # Remove all indexes
            to_del = [idx for idx, info in self.storage.data['nosql_indexes'].items() if info["collection"] == name]
            for idx in to_del:
                del self.storage.data['nosql_indexes'][idx]
            to_del_text = [idx for idx, info in self.storage.data['text_indexes'].items() if info["collection"] == name]
            for idx in to_del_text:
                del self.storage.data['text_indexes'][idx]
            del self.storage.data['collections'][name]
            # Only save immediately if not in a transaction (auto‑commit)
            if not self.active_txn:
                self.storage.save()
            logger.info(f"Dropped collection '{name}'")
            return f"Collection '{name}' dropped successfully"
        except Exception as e:
            logger.error(f"DROP COLLECTION failed: {e}")
            return f"Error: {e}"

    # ---------- CRUD operations with transaction support ----------
    def _insert(self, sql: str) -> str:
        try:
            parsed = NoSQLParser.parse_insert(sql)
            coll = parsed["collection"]
            doc = parsed["doc"]
            if coll not in self.storage.data['collections']:
                return f"Error: Collection '{coll}' does not exist. Create it first."

            # Add a unique ID if missing
            if "_id" not in doc and "id" not in doc:
                # Generate a simple auto‑increment ID per collection
                docs = self.storage.data['collections'][coll]
                next_id = len(docs) + 1
                doc["id"] = next_id

            # Perform insert in memory
            self.storage.data['collections'][coll].append(doc)
            new_idx = len(self.storage.data['collections'][coll]) - 1

            # Update equality indexes
            for idx_name, idx_info in self.storage.data['nosql_indexes'].items():
                if idx_info["collection"] == coll:
                    field = idx_info["field"]
                    val = self._get_nested(doc, field)
                    if val is not None:
                        idx_info["map"].setdefault(val, []).append(new_idx)

            # Update text indexes
            for idx_name, idx_info in self.storage.data['text_indexes'].items():
                if idx_info["collection"] == coll:
                    field = idx_info["field"]
                    text = self._get_nested(doc, field)
                    if isinstance(text, str):
                        words = re.findall(r'\b\w+\b', text.lower())
                        inverted = idx_info["inverted"]
                        for word in words:
                            inverted.setdefault(word, []).append(new_idx)

            # Write WAL entry if inside a transaction
            if self.active_txn:
                self.log.write({
                    "txn_id": self.active_txn,
                    "op": "INSERT",
                    "collection": coll,
                    "doc": doc
                })
                # Do NOT save to disk – defer to commit
            else:
                self.storage.save()

            logger.info(f"Inserted document into '{coll}': {doc}")
            return "Insert successful"
        except Exception as e:
            logger.error(f"INSERT failed: {e}")
            return f"Error: {e}"

    def _update(self, sql: str) -> str:
        try:
            parsed = NoSQLParser.parse_update(sql)
            coll = parsed["collection"]
            set_pairs = parsed["set"]
            where = parsed["where"]

            if coll not in self.storage.data['collections']:
                return f"Error: Collection '{coll}' does not exist"
            docs = self.storage.data['collections'][coll]
            updated = 0
            log_records = []   # collect WAL entries for this transaction

            for idx, doc in enumerate(docs):
                # Evaluate WHERE condition
                if where is not None:
                    field, operator, value = where
                    left_val = self._get_nested(doc, field)
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

                old_doc = doc.copy()
                # Apply SET assignments (supports both "field = value" and array operators)
                for item in set_pairs:
                    if len(item) == 3:
                        field, op, val = item
                        if op == "=":
                            self._set_nested(doc, field, val)
                        elif op == "PUSH":
                            self._array_push(doc, field, val)
                        elif op == "PULL":
                            self._array_pull(doc, field, val)
                        elif op == "ADDTOSET":
                            self._array_add_to_set(doc, field, val)
                    else:
                        # Compatibility: old format (field, value)
                        field, val = item
                        self._set_nested(doc, field, val)

                # Update equality indexes (incremental)
                for idx_name, idx_info in self.storage.data['nosql_indexes'].items():
                    if idx_info["collection"] == coll:
                        field = idx_info["field"]
                        old_val = self._get_nested(old_doc, field)
                        new_val = self._get_nested(doc, field)
                        if old_val != new_val:
                            index_map = idx_info["map"]
                            if old_val is not None and old_val in index_map:
                                if idx in index_map[old_val]:
                                    index_map[old_val].remove(idx)
                                    if not index_map[old_val]:
                                        del index_map[old_val]
                            if new_val is not None:
                                index_map.setdefault(new_val, []).append(idx)

                # If inside a transaction, record the change
                if self.active_txn:
                    log_records.append({
                        "txn_id": self.active_txn,
                        "op": "UPDATE",
                        "collection": coll,
                        "old": old_doc,
                        "new": doc.copy()
                    })
                updated += 1

            # Rebuild text indexes for this collection (simple but correct)
            self._rebuild_text_indexes_for_collection(coll)

            # Write WAL records if in transaction
            if self.active_txn and log_records:
                for rec in log_records:
                    self.log.write(rec)
                # Do NOT save to disk – defer to commit
            else:
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
                to_delete_docs = docs[:]
                self.storage.data['collections'][coll] = []
                # Clear indexes
                for idx_name, idx_info in self.storage.data['nosql_indexes'].items():
                    if idx_info["collection"] == coll:
                        idx_info["map"].clear()
                for idx_name, idx_info in self.storage.data['text_indexes'].items():
                    if idx_info["collection"] == coll:
                        idx_info["inverted"].clear()
                # WAL: if in transaction, log each deleted document
                if self.active_txn:
                    for doc in to_delete_docs:
                        self.log.write({
                            "txn_id": self.active_txn,
                            "op": "DELETE",
                            "collection": coll,
                            "old": doc
                        })
                if not self.active_txn:
                    self.storage.save()
                return f"Delete successful. {len(to_delete_docs)} document(s) deleted."
            else:
                # Find indices to delete
                to_delete_docs = []
                to_delete_indices = []
                for idx, doc in enumerate(docs):
                    doc_val = self._get_nested(doc, where_field)
                    if doc_val == where_value:
                        to_delete_indices.append(idx)
                        to_delete_docs.append(doc)

                # Remove documents
                new_docs = [doc for i, doc in enumerate(docs) if i not in to_delete_indices]
                self.storage.data['collections'][coll] = new_docs

                # Rebuild equality indexes for this collection
                for idx_name, idx_info in self.storage.data['nosql_indexes'].items():
                    if idx_info["collection"] == coll:
                        field = idx_info["field"]
                        new_map = {}
                        for new_idx, doc in enumerate(new_docs):
                            val = self._get_nested(doc, field)
                            if val is not None:
                                new_map.setdefault(val, []).append(new_idx)
                        idx_info["map"] = new_map

                # Rebuild text indexes for this collection
                self._rebuild_text_indexes_for_collection(coll)

                # Write WAL records if in transaction
                if self.active_txn:
                    for doc in to_delete_docs:
                        self.log.write({
                            "txn_id": self.active_txn,
                            "op": "DELETE",
                            "collection": coll,
                            "old": doc
                        })
                    # Do NOT save to disk – defer to commit
                else:
                    self.storage.save()

                return f"Delete successful. {len(to_delete_docs)} document(s) deleted."
        except Exception as e:
            logger.error(f"DELETE failed: {e}")
            return f"Error: {e}"

   # ---------- SELECT with index usage (unchanged) ----------
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

            # ----- Determine if SELECT has aggregates -----
            has_aggregate = False
            if columns_spec != "*":
                for col in columns_spec:
                    if re.match(r"COUNT|SUM|AVG|MIN|MAX", col, re.IGNORECASE):
                        has_aggregate = True
                        break

            # ----- Try to use index for WHERE clause (if no GROUP BY and no aggregates) -----
            if where_cond and not group_by and not has_aggregate:
                # First check for text index (CONTAINS)
                text_idx = self._find_text_index_for_condition(collection, where_cond)
                if text_idx:
                    idx_name, word = text_idx
                    idx_data = self.storage.data['text_indexes'][idx_name]
                    row_indices = idx_data["inverted"].get(word, [])
                    all_docs = self.storage.data['collections'][collection]
                    docs = [all_docs[i] for i in row_indices]
                    where_cond = None
                else:
                    # Then try equality index
                    idx_info = self._find_index_for_condition(collection, where_cond)
                    if idx_info:
                        idx_name, value = idx_info
                        idx_data = self.storage.data['nosql_indexes'][idx_name]
                        row_indices = idx_data["map"].get(value, [])
                        all_docs = self.storage.data['collections'][collection]
                        docs = [all_docs[i] for i in row_indices]
                        where_cond = None

            # Apply remaining WHERE condition (if any)
            if where_cond:
                docs = [d for d in docs if self._evaluate_condition(d, where_cond)]

            # ----- GROUP BY / aggregates (support multiple columns) -----
            if group_by:
                # Ensure group_by is a list (backward compatibility: split string)
                if isinstance(group_by, str):
                    group_cols = [col.strip() for col in group_by.split(',')]
                else:
                    group_cols = group_by   # already a list

                if columns_spec == "*":
                    regular_fields = []
                    agg_specs = {}
                else:
                    regular_fields, agg_specs = self._prepare_aggregates(columns_spec)

                groups = {}
                for doc in docs:
                    # Build composite key as a tuple of values for all group columns
                    key = tuple(self._get_nested(doc, col) for col in group_cols)
                    if key not in groups:
                        groups[key] = []
                    groups[key].append(doc)

                result_docs = []
                for key, group_docs in groups.items():
                    row = {}
                    # Add each group column value (for single column, key is a 1‑tuple)
                    for i, col in enumerate(group_cols):
                        row[col] = key[i]
                    # Non‑aggregate regular fields: take first non‑null from any row in group
                    for f in regular_fields:
                        val = None
                        for d in group_docs:
                            val = self._get_nested(d, f)
                            if val is not None:
                                break
                        row[f] = val
                    # Aggregate functions
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
                if having_cond:
                    docs = [d for d in docs if self._evaluate_condition(d, having_cond)]

            else:
                # No GROUP BY – possible global aggregates
                if columns_spec != "*":
                    regular_fields, agg_specs = self._prepare_aggregates(columns_spec)
                    if agg_specs:
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
                        docs = [row]

            # ORDER BY
            if order_by:
                field, direction = order_by
                reverse = (direction.upper() == "DESC")
                docs.sort(key=lambda d: self._get_nested(d, field), reverse=reverse)

            # LIMIT
            if limit:
                limit_val, offset = limit
                start = offset if offset is not None else 0
                docs = docs[start:start+limit_val]

            # Project specific columns (if no GROUP BY and no aggregates)
            if columns_spec != "*" and not group_by and not agg_specs:
                selected = []
                for doc in docs:
                    new_doc = {}
                    for col in columns_spec:
                        new_doc[col] = self._get_nested(doc, col)
                    selected.append(new_doc)
                docs = selected

            return json.dumps(docs, indent=2, default=str)

        except Exception as e:
            logger.error(f"NoSQL SELECT failed: {e}")
            return f"Error: {e}"

    # ---------- Array operators ----------
    def _array_push(self, doc: Dict, field: str, value: Any):
        current = self._get_nested(doc, field)
        if current is None:
            self._set_nested(doc, field, [value])
        elif isinstance(current, list):
            current.append(value)
            self._set_nested(doc, field, current)
        else:
            # Convert non‑list to list and append
            self._set_nested(doc, field, [current, value])

    def _array_pull(self, doc: Dict, field: str, value: Any):
        current = self._get_nested(doc, field)
        if isinstance(current, list):
            # For strings, case‑insensitive removal
            new_list = []
            for item in current:
                if isinstance(item, str) and isinstance(value, str):
                    if item.lower() != value.lower():
                        new_list.append(item)
                else:
                    if item != value:
                        new_list.append(item)
            self._set_nested(doc, field, new_list)
        # If not a list, nothing to pull

    def _array_add_to_set(self, doc: Dict, field: str, value: Any):
        current = self._get_nested(doc, field)
        if current is None:
            self._set_nested(doc, field, [value])
        elif isinstance(current, list):
            # Check if value already exists (case‑insensitive for strings)
            exists = False
            for item in current:
                if isinstance(item, str) and isinstance(value, str):
                    if item.lower() == value.lower():
                        exists = True
                        break
                else:
                    if item == value:
                        exists = True
                        break
            if not exists:
                current.append(value)
                self._set_nested(doc, field, current)
        else:
            # Convert to list if not already
            self._set_nested(doc, field, [current, value])

    # ---------- Helper methods ----------
    def _get_nested(self, doc: Dict, path: str) -> Any:
        parts = path.split('.')
        current = doc
        for p in parts:
            if isinstance(current, dict) and p in current:
                current = current[p]
            else:
                return None
        return current

    def _set_nested(self, doc: Dict, path: str, value: Any) -> None:
        parts = path.split('.')
        current = doc
        for i, p in enumerate(parts[:-1]):
            if p not in current or not isinstance(current[p], dict):
                current[p] = {}
            current = current[p]
        current[parts[-1]] = value

    def _evaluate_condition(self, doc: Dict, condition_str: str) -> bool:
        if not condition_str:
            return True
        cond = condition_str.strip()
        # First, try to match CONTAINS operator (case‑insensitive, works on arrays and strings)
        contains_match = re.match(r'^([\w.]+)\s+CONTAINS\s+(.+)$', cond, re.IGNORECASE)
        if contains_match:
            field = contains_match.group(1).strip()
            right_raw = contains_match.group(2).strip()
            if right_raw.startswith("'") and right_raw.endswith("'"):
                right_val = right_raw[1:-1]
            else:
                right_val = right_raw
            left_val = self._get_nested(doc, field)
            if left_val is None:
                return False
            if isinstance(left_val, list):
                for item in left_val:
                    if isinstance(item, str) and isinstance(right_val, str):
                        if right_val.lower() in item.lower():
                            return True
                    elif item == right_val:
                        return True
                return False
            elif isinstance(left_val, str):
                return right_val.lower() in left_val.lower()
            else:
                return False

        # Then handle standard operators
        match = re.match(r'^([\w.]+)\s*([=<>!]+)\s*(.+)$', cond)
        if not match:
            return False
        field = match.group(1).strip()
        operator = match.group(2).strip()
        right_raw = match.group(3).strip()
        if right_raw.startswith("'") and right_raw.endswith("'"):
            right_val = right_raw[1:-1]
        elif right_raw.isdigit():
            right_val = int(right_raw)
        else:
            try:
                right_val = float(right_raw)
            except:
                right_val = right_raw
        left_val = self._get_nested(doc, field)
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

    # ---------- Index management ----------
    def _create_index(self, sql: str) -> str:
        try:
            parsed = NoSQLParser.parse_create_index(sql)
            idx_name = parsed["index_name"]
            collection = parsed["collection"]
            field = parsed["field"]
            if collection not in self.storage.data['collections']:
                return f"Error: Collection '{collection}' does not exist"
            if idx_name in self.storage.data['nosql_indexes']:
                return f"Error: Index '{idx_name}' already exists"
            docs = self.storage.data['collections'][collection]
            index_map = {}
            for idx, doc in enumerate(docs):
                val = self._get_nested(doc, field)
                if val is not None:
                    index_map.setdefault(val, []).append(idx)
            self.storage.data['nosql_indexes'][idx_name] = {
                "collection": collection,
                "field": field,
                "map": index_map
            }
            self.storage.save()
            logger.info(f"Created NoSQL index '{idx_name}' on {collection}({field})")
            return f"Index '{idx_name}' created successfully"
        except Exception as e:
            logger.error(f"CREATE INDEX failed: {e}")
            return f"Error: {e}"

    def _drop_index(self, sql: str) -> str:
        try:
            idx_name = NoSQLParser.parse_drop_index(sql)
            if idx_name not in self.storage.data['nosql_indexes']:
                return f"Error: Index '{idx_name}' does not exist"
            del self.storage.data['nosql_indexes'][idx_name]
            self.storage.save()
            logger.info(f"Dropped NoSQL index '{idx_name}'")
            return f"Index '{idx_name}' dropped successfully"
        except Exception as e:
            logger.error(f"DROP INDEX failed: {e}")
            return f"Error: {e}"

    def _find_index_for_condition(self, collection: str, condition_str: str) -> Optional[Tuple[str, Any]]:
        match = re.match(r'^\s*([\w.]+)\s*=\s*(.+)$', condition_str)
        if not match:
            return None
        field = match.group(1)
        value_str = match.group(2).strip()
        if value_str.startswith("'") and value_str.endswith("'"):
            value = value_str[1:-1]
        elif value_str.isdigit():
            value = int(value_str)
        else:
            try:
                value = float(value_str)
            except:
                value = value_str
        for idx_name, idx_info in self.storage.data['nosql_indexes'].items():
            if idx_info["collection"] == collection and idx_info["field"] == field:
                return (idx_name, value)
        return None

    def _create_text_index(self, sql: str) -> str:
        try:
            parsed = NoSQLParser.parse_create_text_index(sql)
            idx_name = parsed["index_name"]
            collection = parsed["collection"]
            field = parsed["field"]
            if collection not in self.storage.data['collections']:
                return f"Error: Collection '{collection}' does not exist"
            if idx_name in self.storage.data['text_indexes']:
                return f"Error: Text index '{idx_name}' already exists"
            docs = self.storage.data['collections'][collection]
            inverted = {}
            for idx, doc in enumerate(docs):
                text = self._get_nested(doc, field)
                if isinstance(text, str):
                    words = re.findall(r'\b\w+\b', text.lower())
                    for word in words:
                        inverted.setdefault(word, []).append(idx)
            self.storage.data['text_indexes'][idx_name] = {
                "collection": collection,
                "field": field,
                "inverted": inverted
            }
            self.storage.save()
            logger.info(f"Created text index '{idx_name}' on {collection}({field})")
            return f"Text index '{idx_name}' created successfully"
        except Exception as e:
            logger.error(f"CREATE TEXT INDEX failed: {e}")
            return f"Error: {e}"

    def _drop_text_index(self, sql: str) -> str:
        try:
            idx_name = NoSQLParser.parse_drop_text_index(sql)
            if idx_name not in self.storage.data['text_indexes']:
                return f"Error: Text index '{idx_name}' does not exist"
            del self.storage.data['text_indexes'][idx_name]
            self.storage.save()
            logger.info(f"Dropped text index '{idx_name}'")
            return f"Text index '{idx_name}' dropped successfully"
        except Exception as e:
            logger.error(f"DROP TEXT INDEX failed: {e}")
            return f"Error: {e}"

    def _find_text_index_for_condition(self, collection: str, condition_str: str) -> Optional[Tuple[str, str]]:
        match = re.match(r'^\s*([\w.]+)\s+CONTAINS\s+(.+)$', condition_str, re.IGNORECASE)
        if not match:
            return None
        field = match.group(1)
        word_str = match.group(2).strip()
        if word_str.startswith("'") and word_str.endswith("'"):
            word = word_str[1:-1].lower()
        else:
            word = word_str.lower()
        for idx_name, idx_info in self.storage.data['text_indexes'].items():
            if idx_info["collection"] == collection and idx_info["field"] == field:
                return (idx_name, word)
        return None

    def _rebuild_text_indexes_for_collection(self, collection: str):
        for idx_name, idx_info in self.storage.data['text_indexes'].items():
            if idx_info["collection"] == collection:
                field = idx_info["field"]
                docs = self.storage.data['collections'][collection]
                inverted = {}
                for idx, doc in enumerate(docs):
                    text = self._get_nested(doc, field)
                    if isinstance(text, str):
                        words = re.findall(r'\b\w+\b', text.lower())
                        for word in words:
                            inverted.setdefault(word, []).append(idx)
                idx_info["inverted"] = inverted
        self.storage.save()

    # ---------- Statistics ----------
    def get_stats(self) -> Dict[str, Any]:
        collections = self.storage.data.get('collections', {})
        indexes = self.storage.data.get('nosql_indexes', {})
        text_indexes = self.storage.data.get('text_indexes', {})

        collection_stats = {}
        for coll_name, docs in collections.items():
            collection_stats[coll_name] = {
                "document_count": len(docs),
                "indexes": [idx for idx, info in indexes.items() if info.get("collection") == coll_name],
                "text_indexes": [idx for idx, info in text_indexes.items() if info.get("collection") == coll_name]
            }

        return {
            "collections": collection_stats,
            "total_collections": len(collections),
            "total_indexes": len(indexes),
            "total_text_indexes": len(text_indexes)
        }