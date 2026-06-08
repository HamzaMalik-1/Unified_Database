# backend/engine/sql_engine.py
from storage.storage import Storage
from data_types.data_types import DataType
from constraints.sql_constraints import SQLConstraint
from parser.sql_parser import SQLParser
from loguru import logger
from typing import Dict, List, Tuple, Any, Optional
import re
import json
from utils.response import ResponseFactory, APIResponse

class SQLDatabase:
    def __init__(self, storage: Storage):
        self.storage = storage
        if 'indexes' not in self.storage.data:
            self.storage.data['indexes'] = {}
        if 'foreign_keys' not in self.storage.data:
            self.storage.data['foreign_keys'] = {}
        if 'check_constraints' not in self.storage.data:
            self.storage.data['check_constraints'] = {}   # <-- FIXED
        if 'auto_increment' not in self.storage.data:
            self.storage.data['auto_increment'] = {}
        if 'views' not in self.storage.data:
            self.storage.data['views'] = {}
        if 'procedures' not in self.storage.data:
            self.storage.data['procedures'] = {}
        if 'triggers' not in self.storage.data:
            self.storage.data['triggers'] = []
        self.storage.save()
        self.nosql_engine = None

    def set_nosql_engine(self, nosql_engine):
        self.nosql_engine = nosql_engine

    def execute(self, command: str) -> APIResponse:
        cmd_upper = command.strip().upper()
        try:
            if cmd_upper.startswith("CREATE TABLE"):
                result = self._create_table(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            # ----- CTE (WITH) support -----
            elif cmd_upper.startswith("WITH"):
                result_str = self._execute_with(command)
                if result_str.startswith("Error:"):
                    return ResponseFactory.error(result_str, status=400)
                try:
                    rows = json.loads(result_str)
                    if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
                        return ResponseFactory.table(rows, message="Query executed successfully")
                    else:
                        return ResponseFactory.json(rows, message="Query executed successfully")
                except:
                    return ResponseFactory.text(result_str, message="Query executed successfully")
            
            # Check for set operations (UNION, INTERSECT, EXCEPT) before normal SELECT
            elif cmd_upper.startswith("SELECT") and any(kw in cmd_upper for kw in ('UNION', 'INTERSECT', 'EXCEPT')):
                result_str = self._execute_set_operation(command)
                if result_str.startswith("Error:"):
                    return ResponseFactory.error(result_str, status=400)
                try:
                    rows = json.loads(result_str)
                    if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
                        return ResponseFactory.table(rows, message="Query executed successfully")
                    else:
                        return ResponseFactory.json(rows, message="Query executed successfully")
                except:
                    return ResponseFactory.text(result_str, message="Query executed successfully")
            
            
            elif cmd_upper.startswith("INSERT INTO") and "SELECT" in cmd_upper and "FROM NOSQL" in cmd_upper:
                result = self._insert_from_nosql(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("INSERT INTO"):
                result = self._insert(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("SELECT"):
                result_str = self._select(command)
                if result_str.startswith("Error:"):
                    return ResponseFactory.error(result_str, status=400)
                try:
                    rows = json.loads(result_str)
                    if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
                        return ResponseFactory.table(rows, message="Query executed successfully")
                    else:
                        return ResponseFactory.json(rows, message="Query executed successfully")
                except:
                    return ResponseFactory.text(result_str, message="Query executed successfully")
            elif cmd_upper.startswith("UPDATE"):
                result = self._update(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("DELETE FROM"):
                result = self._delete(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("DROP TABLE"):
                result = self._drop_table(command)
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
            elif cmd_upper.startswith("ALTER TABLE"):
                result = self._alter_table(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("CREATE VIEW"):
                result = self._create_view(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("DROP VIEW"):
                result = self._drop_view(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            
            elif cmd_upper.startswith("CREATE PROCEDURE"):
                result = self._create_procedure(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("DROP PROCEDURE"):
                result = self._drop_procedure(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("CALL"):
                return self._call_procedure(command)
            
            elif cmd_upper.startswith("CREATE TRIGGER"):
                result = self._create_trigger(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            elif cmd_upper.startswith("DROP TRIGGER"):
                result = self._drop_trigger(command)
                if result.startswith("Error:"):
                    return ResponseFactory.error(result, status=400)
                return ResponseFactory.text(result)
            else:
                return ResponseFactory.error(f"Unsupported SQL command: {command}", status=400)
        except Exception as e:
            logger.error(f"SQL execution failed: {e}")
            return ResponseFactory.error(str(e), status=500)

    def _create_table(self, sql: str) -> str:
            try:
                parsed = SQLParser.parse_create_table(sql)
                table_name = parsed["table_name"]
                columns = parsed["columns"]
                foreign_keys = parsed.get("foreign_keys", [])
                checks = parsed.get("checks", [])                     # <-- ADDED
                if self.storage.get_schema(table_name):
                    return f"Error: Table '{table_name}' already exists"
                self.storage.set_schema(table_name, columns)
                self.storage.set_table_data(table_name, [])
                self.storage.data['foreign_keys'][table_name] = foreign_keys
                self.storage.data['check_constraints'][table_name] = checks   # <-- ADDED
                for col in columns:
                    if col.get('auto_increment'):
                        self.storage.data['auto_increment'][table_name] = 0
                        break
                self.storage.save()
                logger.info(f"Table '{table_name}' created with {len(foreign_keys)} foreign keys, {len(checks)} checks")
                return f"Table '{table_name}' created successfully"
            except Exception as e:
                logger.error(f"CREATE TABLE failed: {e}")
                return f"Error: {e}"

    def _insert(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_insert(sql)
            table_name = parsed["table_name"]
            specified_cols = parsed["columns"]
            raw_values = parsed["values"]
            schema = self.storage.get_schema(table_name)
            if not schema:
                return f"Error: Table '{table_name}' does not exist"
            existing_rows = self.storage.get_table_data(table_name)

            if not specified_cols:
                target_cols = [col["name"] for col in schema]
                if len(raw_values) != len(target_cols):
                    return f"Error: Expected {len(target_cols)} values, got {len(raw_values)}"
                value_dict = dict(zip(target_cols, raw_values))
            else:
                schema_col_names = [col["name"] for col in schema]
                for col in specified_cols:
                    if col not in schema_col_names:
                        return f"Error: Column '{col}' does not exist"
                if len(specified_cols) != len(raw_values):
                    return f"Error: Number of columns ({len(specified_cols)}) != number of values ({len(raw_values)})"
                value_dict = dict(zip(specified_cols, raw_values))

            row = {}
            for col_def in schema:
                col_name = col_def["name"]
                col_type = col_def["type"]
                constraints = col_def["constraints"]
                val = value_dict.get(col_name, None)

                # ----- Auto‑increment (highest priority) -----
                if col_def.get('auto_increment'):
                    if val is None:
                        counter = self.storage.data['auto_increment'].get(table_name, 0) + 1
                        self.storage.data['auto_increment'][table_name] = counter
                        typed_val = counter
                    else:
                        try:
                            user_val = int(val)
                        except (ValueError, TypeError):
                            return f"Error: Invalid integer for auto‑increment column '{col_name}'"
                        current = self.storage.data['auto_increment'].get(table_name, 0)
                        if user_val > current:
                            self.storage.data['auto_increment'][table_name] = user_val
                        typed_val = user_val
                else:
                    if val is None and col_def.get("default") is not None:
                        default_val = col_def["default"]
                        try:
                            typed_val = DataType.validate(default_val, col_type)
                        except:
                            typed_val = default_val
                    else:
                        try:
                            typed_val = DataType.validate(val, col_type) if val is not None else None
                        except ValueError as e:
                            return f"Error in column '{col_name}': {e}"

                # ----- Constraints -----
                if "NOT NULL" in constraints and typed_val is None:
                    return f"Error: Column '{col_name}' cannot be NULL"
                if "PRIMARY KEY" in constraints:
                    try:
                        SQLConstraint.check_primary_key(typed_val, col_name, existing_rows, len(existing_rows))
                    except ValueError as e:
                        return f"Error: {e}"
                if "UNIQUE" in constraints and "PRIMARY KEY" not in constraints:
                    try:
                        SQLConstraint.check_unique(typed_val, col_name, existing_rows, len(existing_rows))
                    except ValueError as e:
                        return f"Error: {e}"
                row[col_name] = typed_val

            # ---------- Fire BEFORE INSERT triggers ----------
            self._fire_triggers(table_name, "BEFORE", "INSERT", new_row=row)

            # CHECK constraints and foreign keys
            error = self._check_constraints(table_name, row)
            if error:
                return error
            for col_name, col_val in row.items():
                if not self._check_parent_exists(table_name, col_name, col_val):
                    return f"Error: Foreign key violation on {col_name}: value {col_val} not found"

            existing_rows.append(row)
            self.storage.set_table_data(table_name, existing_rows)
            logger.info(f"Inserted row into '{table_name}': {row}")

            # ---------- Fire AFTER INSERT triggers ----------
            self._fire_triggers(table_name, "AFTER", "INSERT", new_row=row)

            return "Insert successful"
        except Exception as e:
            logger.error(f"INSERT failed: {e}")
            return f"Error: {e}"

    def _get_nested(self, doc: Dict, path: str) -> Any:
        """Retrieve a value from a document using dot notation.
        For SQL engine, we only need top‑level keys, but this method
        exists to satisfy calls from _evaluate_condition (which was copied from NoSQL).
        """
        # If the path contains a dot, treat as top‑level key? In SQL we don't need nested.
        # For simplicity, just return the top‑level value.
        return doc.get(path, None)

    def _evaluate_condition(self, row: Dict, condition_str: str) -> bool:
        """
        Evaluate a WHERE condition for a single row (SQL engine).
        Supports standard comparison operators (==, !=, >, <, >=, <=)
        and logical AND/OR via eval (safe because row values are sanitised).
        """
        if not condition_str:
            return True
        cond = condition_str.strip()
        # Replace single '=' with '==' but keep '<=', '>=', '!=', etc.
        cond = re.sub(r'(?<![<>!=])=(?![=])', '==', cond)
        try:
            result = eval(cond, {"__builtins__": {}}, row)
            return bool(result)
        except Exception as e:
            logger.error(f"Condition evaluation failed: {cond} -> {e}")
            return False

    def _apply_aggregates(self, rows: List[Dict], group_col: str, agg_specs: Dict[str, str]) -> List[Dict]:
        groups = {}
        for row in rows:
            key = row.get(group_col)
            if key not in groups:
                groups[key] = []
            groups[key].append(row)
        result = []
        for key, group_rows in groups.items():
            group_result = {group_col: key}
            for col, func in agg_specs.items():
                values = [r.get(col) for r in group_rows if r.get(col) is not None]
                if func.upper() == "COUNT":
                    group_result[col] = len(group_rows)
                elif func.upper() == "SUM":
                    group_result[col] = sum(values) if values else 0
                elif func.upper() == "AVG":
                    group_result[col] = sum(values)/len(values) if values else 0
                elif func.upper() == "MIN":
                    group_result[col] = min(values) if values else None
                elif func.upper() == "MAX":
                    group_result[col] = max(values) if values else None
            result.append(group_result)
        return result

    def _parse_aggregates(self, columns: List[str]) -> Tuple[List[str], Dict[str, Tuple[str, str]]]:
        regular = []
        aggregates = {}
        agg_patterns = {
            "COUNT": r"COUNT\(\s*(?:\*|\w+)\s*\)",
            "SUM": r"SUM\(\s*(\w+)\s*\)",
            "AVG": r"AVG\(\s*(\w+)\s*\)",
            "MIN": r"MIN\(\s*(\w+)\s*\)",
            "MAX": r"MAX\(\s*(\w+)\s*\)"
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

    def _select(self, sql: str, depth: int = 0) -> str:
        try:
            parsed = SQLParser.parse_select(sql)
            main_table = parsed.get("main_table", parsed.get("table"))
            joins = parsed.get("joins", [])
            where_cond = parsed["where"]
            order_by = parsed["order_by"]
            limit = parsed["limit"]
            group_by = parsed["group_by"]
            having_cond = parsed["having"]
            columns_spec = parsed["columns"]
            distinct = parsed.get("distinct", False)

            # Fallback: if DISTINCT appears in the original SQL but flag is missing, force it
            if not distinct and " DISTINCT " in sql.upper():
                distinct = True

            # ---------- View expansion ----------
            is_view = False
            if main_table in self.storage.data.get('views', {}):
                if depth > 5:
                    return "Error: View recursion depth exceeded (possible circular reference?)"
                view_query = self.storage.data['views'][main_table]
                view_result_str = self._select(view_query, depth + 1)
                if view_result_str.startswith("Error:"):
                    return view_result_str
                rows = json.loads(view_result_str)
                is_view = True
            else:
                if not self.storage.get_schema(main_table):
                    return f"Error: Table or view '{main_table}' does not exist"
                rows = self.storage.get_table_data(main_table)
                if not rows:
                    rows = []
                for join_type, join_table, condition in joins:
                    right_rows = self.storage.get_table_data(join_table)
                    if not right_rows:
                        right_rows = []
                    rows = self._join_rows(rows, right_rows, join_type, condition)
                if not rows:
                    return "[]"

            # ----- Index usage -----
            if where_cond and not joins and not group_by and not is_view:
                index_info = self._find_index_for_condition(main_table, where_cond)
                if index_info:
                    index_name, value = index_info
                    idx_data = self.storage.data['indexes'][index_name]
                    row_indices = idx_data["map"].get(value, [])
                    all_rows = self.storage.get_table_data(main_table)
                    rows = [all_rows[i] for i in row_indices]
                    where_cond = None

            # ----- Cross‑model IN subqueries (SQL with NoSQL) -----
            if where_cond and "NOSQL SELECT" in where_cond.upper() and self.nosql_engine:
                pattern = r"(\w+)\s+IN\s*\(\s*NOSQL\s+(SELECT.+?)\s*\)"
                match = re.search(pattern, where_cond, re.IGNORECASE | re.DOTALL)
                if match:
                    outer_column = match.group(1)
                    inner_nosql_sql = match.group(2).strip()
                    resp = self.nosql_engine.execute(inner_nosql_sql)
                    if not resp.success:
                        return f"Error in cross-model subquery: {resp.message}"
                    values = []
                    if resp.data.type == "table":
                        values = [row.get(list(row.keys())[0]) for row in resp.data.content if row]
                    elif resp.data.type == "json":
                        if isinstance(resp.data.content, list):
                            values = [list(item.values())[0] for item in resp.data.content if item]
                    rows = [r for r in rows if r.get(outer_column) in values]
                    where_cond = None

            # ----- scalar subquery extraction -----
            scalar_subqueries = []
            final_columns = []
            if columns_spec != "*":
                for col in columns_spec:
                    col = col.strip()
                    if col.startswith('('):
                        depth_paren = 0
                        end_idx = -1
                        for i, ch in enumerate(col):
                            if ch == '(':
                                depth_paren += 1
                            elif ch == ')':
                                depth_paren -= 1
                                if depth_paren == 0:
                                    end_idx = i
                                    break
                        if end_idx != -1:
                            inner_sql = col[1:end_idx].strip()
                            rest = col[end_idx+1:].strip()
                            alias = None
                            if rest.upper().startswith('AS'):
                                alias = rest[2:].strip()
                            elif rest:
                                alias = rest
                            if alias and inner_sql.upper().startswith('SELECT'):
                                scalar_subqueries.append((alias, inner_sql))
                                final_columns.append(alias)
                            else:
                                final_columns.append(col)
                        else:
                            final_columns.append(col)
                    else:
                        final_columns.append(col)
            else:
                final_columns = "*"

            # ----- multi‑column IN -----
            if where_cond and re.search(r"\(\s*[\w.]+\s*,\s*[\w.]+\s*\)\s+IN\s*\(", where_cond, re.IGNORECASE):
                pattern = r"\(\s*([\w.,\s]+)\s*\)\s+IN\s*\(\s*(SELECT.+?)\s*\)"
                match = re.search(pattern, where_cond, re.IGNORECASE | re.DOTALL)
                if match:
                    col_list_str = match.group(1).strip()
                    subquery_sql = match.group(2).strip()
                    outer_cols = [c.strip() for c in col_list_str.split(',')]
                    sub_rows = self._execute_select_to_rows(subquery_sql, depth+1)
                    value_tuples = [tuple(r.values()) for r in sub_rows]
                    rows = [r for r in rows if tuple(r.get(c) for c in outer_cols) in value_tuples]
                    where_cond = None

            # ----- uncorrelated IN -----
            if where_cond and "IN (SELECT" in where_cond.upper():
                pattern = r"(\w+)\s+IN\s*\(\s*(SELECT\s+.+?)\s*\)"
                match = re.search(pattern, where_cond, re.IGNORECASE | re.DOTALL)
                if match:
                    outer_column = match.group(1)
                    inner_sql = match.group(2).strip()
                    try:
                        inner_rows = self._execute_select_to_rows(inner_sql, depth+1)
                        values = []
                        for row in inner_rows:
                            if outer_column in row:
                                values.append(row[outer_column])
                            elif len(row) > 0:
                                values.append(list(row.values())[0])
                        rows = [r for r in rows if r.get(outer_column) in values]
                        where_cond = None
                    except Exception as e:
                        logger.error(f"Subquery execution failed: {e}")
                        return f"Error in subquery: {e}"

                    # ----- correlated EXISTS / NOT EXISTS -----
            if where_cond and re.search(r'\b(NOT\s+)?EXISTS\s*\(', where_cond, re.IGNORECASE):
                # Find the first EXISTS (or NOT EXISTS) pattern
                exists_pattern = r'(\bNOT\s+)?EXISTS\s*\(\s*(SELECT\s+.+?)\s*\)'
                match = re.search(exists_pattern, where_cond, re.IGNORECASE | re.DOTALL)
                if match:
                    not_flag = match.group(1) is not None
                    subquery_sql = match.group(2).strip()
                    filtered_rows = []
                    for row in rows:
                        exists = self._evaluate_exists(subquery_sql, row, main_table, depth)
                        if not_flag:
                            exists = not exists
                        if exists:
                            filtered_rows.append(row)
                    rows = filtered_rows
                    where_cond = None   # Remove the condition because it has been applied

            # Apply WHERE
            if where_cond:
                rows = [r for r in rows if self._evaluate_condition(r, where_cond)]

            # Attach scalar subquery values
            if scalar_subqueries:
                for alias, sub_sql in scalar_subqueries:
                    scalar_val = self._evaluate_scalar_subquery(sub_sql, depth+1)
                    for row in rows:
                        row[alias] = scalar_val

            # GROUP BY / aggregates
            if group_by:
                if columns_spec == "*":
                    regular_cols = []
                    agg_specs = {}
                else:
                    regular_cols, agg_specs = self._parse_aggregates(columns_spec)
                groups = {}
                for row in rows:
                    key = row.get(group_by)
                    if key not in groups:
                        groups[key] = []
                    groups[key].append(row)
                result_rows = []
                for key, group_rows in groups.items():
                    row_result = {group_by: key}
                    for col in regular_cols:
                        val = next((r.get(col) for r in group_rows if r.get(col) is not None), None)
                        row_result[col] = val
                    for alias, (func, src_col) in agg_specs.items():
                        if src_col == "count":
                            values = [1] * len(group_rows)
                        else:
                            values = [r.get(src_col) for r in group_rows if r.get(src_col) is not None]
                        if func == "COUNT":
                            result = len(group_rows)
                        elif func == "SUM":
                            result = sum(values) if values else 0
                        elif func == "AVG":
                            result = sum(values) / len(values) if values else 0
                        elif func == "MIN":
                            result = min(values) if values else None
                        elif func == "MAX":
                            result = max(values) if values else None
                        row_result[alias] = result
                    result_rows.append(row_result)
                rows = result_rows
                if having_cond:
                    rows = [r for r in rows if self._evaluate_condition(r, having_cond)]
            else:
                if columns_spec != "*":
                    regular_cols, agg_specs = self._parse_aggregates(columns_spec)
                    if agg_specs:
                        row_result = {}
                        for alias, (func, src_col) in agg_specs.items():
                            if src_col == "count":
                                values = [1] * len(rows)
                            else:
                                values = [r.get(src_col) for r in rows if r.get(src_col) is not None]
                            if func == "COUNT":
                                result = len(rows)
                            elif func == "SUM":
                                result = sum(values) if values else 0
                            elif func == "AVG":
                                result = sum(values) / len(values) if values else 0
                            elif func == "MIN":
                                result = min(values) if values else None
                            elif func == "MAX":
                                result = max(values) if values else None
                            row_result[alias] = result
                        rows = [row_result]

            # ========== FINAL COLUMN PROJECTION ==========
            # (apply only if there is no GROUP BY and no aggregate functions)
            if final_columns != "*" and not group_by and not agg_specs:
                out_cols = final_columns
                projected_rows = []
                for row in rows:
                    new_row = {col: row.get(col) for col in out_cols}
                    projected_rows.append(new_row)
                rows = projected_rows

            # ========== DISTINCT (remove duplicate rows based on projected columns) ==========
            if distinct:
                seen = set()
                unique_rows = []
                for row in rows:
                    # Use sorted items to make row hashable and order-independent
                    row_tuple = tuple(sorted(row.items()))
                    if row_tuple not in seen:
                        seen.add(row_tuple)
                        unique_rows.append(row)
                rows = unique_rows

            # ORDER BY
            if order_by:
                col, direction = order_by
                reverse = (direction.upper() == "DESC")
                rows.sort(key=lambda r: r.get(col), reverse=reverse)

            # LIMIT
            if limit:
                limit_val, offset = limit
                start = offset if offset is not None else 0
                rows = rows[start:start+limit_val]

            return json.dumps(rows, indent=2, default=str)

        except Exception as e:
            logger.error(f"SELECT failed: {e}")
            return f"Error: {e}"
            
    def _update(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_update(sql)
            table_name = parsed["table"]
            set_assignments = parsed["set"]
            where_cond = parsed["where"]

            schema = self.storage.get_schema(table_name)
            if not schema:
                return f"Error: Table '{table_name}' does not exist"
            rows = self.storage.get_table_data(table_name)
            if not rows:
                return "Update completed (0 rows affected)"

            # ---------- Cross‑model IN subqueries (SQL with NoSQL) ----------
            if where_cond and "NOSQL SELECT" in where_cond.upper() and self.nosql_engine:
                import re
                pattern = r"(\w+)\s+IN\s*\(\s*NOSQL\s+(SELECT.+?)\s*\)"
                match = re.search(pattern, where_cond, re.IGNORECASE | re.DOTALL)
                if match:
                    outer_column = match.group(1)
                    inner_nosql_sql = match.group(2).strip()
                    # Execute NoSQL command (the inner SELECT without the NOSQL prefix)
                    resp = self.nosql_engine.execute(inner_nosql_sql)
                    if not resp.success:
                        return f"Error in cross-model subquery: {resp.message}"
                    values = []
                    if resp.data.type == "table":
                        values = [row.get(list(row.keys())[0]) for row in resp.data.content if row]
                    elif resp.data.type == "json":
                        if isinstance(resp.data.content, list):
                            values = [list(item.values())[0] for item in resp.data.content if item]
                    # Pre‑filter rows
                    rows = [r for r in rows if r.get(outer_column) in values]
                    where_cond = None
            # -----------------------------------------------------------------

            # Validate column existence and types
            schema_cols = {col["name"]: col for col in schema}
            for col in set_assignments.keys():
                if col not in schema_cols:
                    return f"Error: Column '{col}' does not exist in table '{table_name}'"

            updated_count = 0
            for row in rows:
                # Check WHERE condition (if any left)
                if where_cond and not self._evaluate_condition(row, where_cond):
                    continue

                # Apply updates to a copy to avoid partial updates if constraints fail
                updated_row = row.copy()
                for col, val in set_assignments.items():
                    col_def = schema_cols[col]
                    try:
                        typed_val = DataType.validate(val, col_def["type"]) if val is not None else None
                    except ValueError as e:
                        return f"Error in column '{col}': {e}"
                    if typed_val is None and "NOT NULL" in col_def["constraints"]:
                        return f"Error: Column '{col}' cannot be NULL"
                    # Foreign key check
                    if not self._check_parent_exists(table_name, col, typed_val):
                        return f"Error: Foreign key violation on {col}: value {typed_val} not found in parent table"
                    updated_row[col] = typed_val

                # CHECK CONSTRAINTS AFTER UPDATE
                error = self._check_constraints(table_name, updated_row)
                if error:
                    return error

                # ---------- Fire BEFORE UPDATE trigger ----------
                self._fire_triggers(table_name, "BEFORE", "UPDATE", old_row=row, new_row=updated_row)

                # Apply the update to the real row
                for col, val in set_assignments.items():
                    row[col] = updated_row[col]

                # ---------- Fire AFTER UPDATE trigger ----------
                self._fire_triggers(table_name, "AFTER", "UPDATE", old_row=row, new_row=updated_row)

                updated_count += 1

            self.storage.set_table_data(table_name, rows)
            logger.info(f"Updated {updated_count} row(s) in '{table_name}'")
            return f"Update successful. {updated_count} row(s) affected."
        except Exception as e:
            logger.error(f"UPDATE failed: {e}")
            return f"Error: {e}"
        
    def _delete(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_delete(sql)
            table_name = parsed["table"]
            where_cond = parsed["where"]

            if not self.storage.get_schema(table_name):
                return f"Error: Table '{table_name}' does not exist"
            rows = self.storage.get_table_data(table_name)
            if not rows:
                return "Delete completed (0 rows affected)"

            # ---------- Cross‑model IN subqueries (SQL with NoSQL) ----------
            if where_cond and "NOSQL SELECT" in where_cond.upper() and self.nosql_engine:
                import re
                pattern = r"(\w+)\s+IN\s*\(\s*NOSQL\s+(SELECT.+?)\s*\)"
                match = re.search(pattern, where_cond, re.IGNORECASE | re.DOTALL)
                if match:
                    outer_column = match.group(1)
                    inner_nosql_sql = match.group(2).strip()
                    resp = self.nosql_engine.execute(inner_nosql_sql)
                    if not resp.success:
                        return f"Error in cross-model subquery: {resp.message}"
                    values = []
                    if resp.data.type == "table":
                        values = [row.get(list(row.keys())[0]) for row in resp.data.content if row]
                    elif resp.data.type == "json":
                        if isinstance(resp.data.content, list):
                            values = [list(item.values())[0] for item in resp.data.content if item]
                    # Pre‑filter rows
                    rows = [r for r in rows if r.get(outer_column) in values]
                    where_cond = None
            # ---------------------------------------------------------------

            # Identify rows to delete (apply remaining WHERE condition, if any)
            to_delete = []
            for row in rows:
                if where_cond is None or self._evaluate_condition(row, where_cond):
                    to_delete.append(row)

            # Check foreign key dependencies (RESTRICT/NO ACTION)
            for row in to_delete:
                for col_name, col_val in row.items():
                    for child_table, fk_list in self.storage.data['foreign_keys'].items():
                        for fk in fk_list:
                            if fk["ref_table"] == table_name and fk["ref_column"] == col_name:
                                child_col = fk["column"]
                                action = fk["on_delete"]
                                if action in ("RESTRICT", "NO ACTION"):
                                    child_rows = self.storage.get_table_data(child_table)
                                    for child_row in child_rows:
                                        if child_row.get(child_col) == col_val:
                                            return f"Error: Cannot delete row because child row exists in {child_table}.{child_col}"

            # Fire BEFORE DELETE triggers
            for row in to_delete:
                self._fire_triggers(table_name, "BEFORE", "DELETE", old_row=row)

            # Delete rows
            new_rows = [r for r in rows if r not in to_delete]
            self.storage.set_table_data(table_name, new_rows)

            # Fire AFTER DELETE triggers
            for row in to_delete:
                self._fire_triggers(table_name, "AFTER", "DELETE", old_row=row)

            # Handle CASCADE / SET NULL after deletion
            for row in to_delete:
                for col_name, col_val in row.items():
                    self._handle_child_references(table_name, col_name, col_val, is_delete=True)

            self.storage.save()
            logger.info(f"Deleted {len(to_delete)} row(s) from '{table_name}'")
            return f"Delete successful. {len(to_delete)} row(s) deleted."
        except Exception as e:
            logger.error(f"DELETE failed: {e}")
            return f"Error: {e}"

    def _check_child_references_before_delete(self, parent_table: str, parent_row: Dict):
        """Before deleting a parent row, ensure child references are allowed (RESTRICT/NO ACTION)."""
        for child_table, fk_list in self.storage.data['foreign_keys'].items():
            for fk in fk_list:
                if fk["ref_table"] == parent_table:
                    child_col = fk["column"]
                    action = fk["on_delete"]
                    if action in ("RESTRICT", "NO ACTION"):
                        child_rows = self.storage.get_table_data(child_table)
                        for child_row in child_rows:
                            if child_row.get(child_col) == parent_row.get(fk["ref_column"]):
                                raise Exception(f"Referenced row exists in {child_table}.{child_col}; cannot delete parent")

    def _cascade_delete(self, parent_table: str, parent_row: Dict):
        """Perform CASCADE or SET NULL on child tables when parent row is deleted."""
        for child_table, fk_list in self.storage.data['foreign_keys'].items():
            for fk in fk_list:
                if fk["ref_table"] == parent_table:
                    child_col = fk["column"]
                    action = fk["on_delete"]
                    if action == "CASCADE":
                        child_rows = self.storage.get_table_data(child_table)
                        new_child_rows = []
                        for child_row in child_rows:
                            if child_row.get(child_col) != parent_row.get(fk["ref_column"]):
                                new_child_rows.append(child_row)
                        self.storage.set_table_data(child_table, new_child_rows)
                    elif action == "SET NULL":
                        child_rows = self.storage.get_table_data(child_table)
                        for child_row in child_rows:
                            if child_row.get(child_col) == parent_row.get(fk["ref_column"]):
                                child_row[child_col] = None
                        self.storage.set_table_data(child_table, child_rows)

    def _drop_table(self, sql: str) -> str:
        try:
            table_name = SQLParser.parse_drop_table(sql)
            if not self.storage.get_schema(table_name):
                return f"Error: Table '{table_name}' does not exist"
            # Also remove foreign keys referencing this table? For simplicity, we remove the table's own fk definitions.
            if table_name in self.storage.data['foreign_keys']:
                del self.storage.data['foreign_keys'][table_name]
            del self.storage.data["schemas"][table_name]
            del self.storage.data["tables"][table_name]
            self.storage.save()
            logger.info(f"Dropped table '{table_name}'")
            return f"Table '{table_name}' dropped successfully"
        except Exception as e:
            logger.error(f"DROP TABLE failed: {e}")
            return f"Error: {e}"

    def _handle_subqueries(self, where_cond: str, outer_row: Dict = None, depth: int = 0) -> Tuple[str, bool]:
        if not where_cond:
            return where_cond, True
        in_match = re.search(r"(\w+)\s+IN\s*\(\s*(SELECT .+?)\s*\)", where_cond, re.IGNORECASE | re.DOTALL)
        if in_match:
            column = in_match.group(1)
            sub_sql = in_match.group(2)
            sub_result = self._select(sub_sql, depth+1)
            import json
            try:
                rows = json.loads(sub_result)
                values = [row.get(column) for row in rows if column in row]
                if not values:
                    new_cond = "1=0"
                else:
                    vals_str = ','.join(str(v) for v in values)
                    new_cond = f"{column} IN ({vals_str})"
                new_where = where_cond[:in_match.start()] + new_cond + where_cond[in_match.end():]
                return new_where, True
            except:
                pass
        return where_cond, True

    def _execute_select_to_rows(self, sql: str, depth: int = 0) -> List[Dict]:
        result_str = self._select(sql, depth)
        if result_str.startswith("Error:"):
            raise Exception(result_str)
        return json.loads(result_str)

    def _evaluate_scalar_subquery(self, subquery_sql: str, depth: int = 0) -> Any:
        rows = self._execute_select_to_rows(subquery_sql, depth)
        if not rows:
            return None
        row = rows[0]
        return list(row.values())[0] if row else None

    def _evaluate_join_condition(self, row1: Dict, row2: Dict, condition: str,
                                  alias1: str = None, alias2: str = None) -> bool:
        """
        Evaluate a JOIN ON condition for a pair of rows.

        Strategy:
          1. Extract all "table.column" references from the condition string itself.
          2. Determine which table name maps to row1 vs row2 by checking which
             referenced columns exist in each row's keys.
          3. Substitute qualified references (table.column → value) first.
          4. Substitute any remaining unqualified references, sorting by column
             name length descending to prevent partial matches (e.g. department_id
             must be replaced before id).
          5. Fix single '=' → '==' and eval.
        """
        if not condition:
            return True

        cond = condition

        # Step 1: Extract all "word.word" qualified references from the condition
        qualified_refs = re.findall(r'\b(\w+)\.(\w+)\b', cond)

        if qualified_refs:
            # Unique table names mentioned, preserving first-seen order
            table_names = list(dict.fromkeys(t for t, c in qualified_refs))

            row1_keys = set(row1.keys())
            row2_keys = set(row2.keys())

            row1_table = None
            row2_table = None

            for tname in table_names:
                cols_for_table = [c for t, c in qualified_refs if t == tname]
                row1_matches = sum(1 for c in cols_for_table if c in row1_keys)
                row2_matches = sum(1 for c in cols_for_table if c in row2_keys)

                if row1_matches >= row2_matches:
                    if row1_table is None:
                        row1_table = tname
                    elif row2_table is None:
                        row2_table = tname
                else:
                    if row2_table is None:
                        row2_table = tname
                    elif row1_table is None:
                        row1_table = tname

            # Fallback to aliases if detection failed
            row1_table = row1_table or alias1
            row2_table = row2_table or alias2

            # Step 2: Replace qualified "table.column" → repr(value)
            # Sort by column name length descending to avoid partial replacements
            if row1_table:
                for col_name, col_val in sorted(row1.items(), key=lambda x: len(x[0]), reverse=True):
                    cond = re.sub(
                        rf'\b{re.escape(row1_table)}\.{re.escape(col_name)}\b',
                        repr(col_val), cond
                    )

            if row2_table:
                for col_name, col_val in sorted(row2.items(), key=lambda x: len(x[0]), reverse=True):
                    cond = re.sub(
                        rf'\b{re.escape(row2_table)}\.{re.escape(col_name)}\b',
                        repr(col_val), cond
                    )

        # Step 3: Replace any remaining unqualified column references
        # Negative lookbehind (?<![\w.]) ensures we don't re-match inside already-substituted values
        # Sort longest first: department_id before id
        for col_name, col_val in sorted(row1.items(), key=lambda x: len(x[0]), reverse=True):
            cond = re.sub(rf'(?<![\w.]){re.escape(col_name)}\b', repr(col_val), cond)

        for col_name, col_val in sorted(row2.items(), key=lambda x: len(x[0]), reverse=True):
            cond = re.sub(rf'(?<![\w.]){re.escape(col_name)}\b', repr(col_val), cond)

        # Step 4: Fix single = to ==
        cond = re.sub(r'(?<![<>!=])=(?!=)', '==', cond)

        try:
            return bool(eval(cond, {"__builtins__": {}}))
        except Exception as e:
            logger.error(f"JOIN eval failed: '{cond}' → {e}")
            return False

    def _join_rows(self, left_rows: List[Dict], right_rows: List[Dict], join_type: str, condition: str, left_alias: str = None, right_alias: str = None) -> List[Dict]:
        if not left_rows or not right_rows:
            if join_type == 'LEFT':
                right_cols = set()
                if right_rows:
                    right_cols = right_rows[0].keys()
                result = []
                for lr in left_rows:
                    row = lr.copy()
                    for col in right_cols:
                        if col not in row:
                            row[col] = None
                    result.append(row)
                return result
            elif join_type == 'RIGHT':
                left_cols = set()
                if left_rows:
                    left_cols = left_rows[0].keys()
                result = []
                for rr in right_rows:
                    row = {}
                    for col in left_cols:
                        row[col] = None
                    row.update(rr)
                    result.append(row)
                return result
            elif join_type == 'FULL':
                left_cols = set()
                right_cols = set()
                if left_rows:
                    left_cols = left_rows[0].keys()
                if right_rows:
                    right_cols = right_rows[0].keys()
                result = []
                for lr in left_rows:
                    row = lr.copy()
                    for col in right_cols:
                        if col not in row:
                            row[col] = None
                    result.append(row)
                for rr in right_rows:
                    row = {}
                    for col in left_cols:
                        row[col] = None
                    row.update(rr)
                    result.append(row)
                return result
            else:
                return []
        if join_type == 'NATURAL':
            left_cols = set(left_rows[0].keys())
            right_cols = set(right_rows[0].keys())
            common_cols = left_cols.intersection(right_cols)
            result = []
            for lr in left_rows:
                for rr in right_rows:
                    match = True
                    for col in common_cols:
                        if lr.get(col) != rr.get(col):
                            match = False
                            break
                    if match:
                        combined = {**lr, **rr}
                        result.append(combined)
            return result
        if join_type == 'CROSS' or not condition:
            result = []
            for lr in left_rows:
                for rr in right_rows:
                    result.append({**lr, **rr})
            return result
        matches = []
        left_matched = [False] * len(left_rows)
        right_matched = [False] * len(right_rows)
        for i, lr in enumerate(left_rows):
            for j, rr in enumerate(right_rows):
                if self._evaluate_join_condition(lr, rr, condition, left_alias, right_alias):
                    matches.append((i, j, {**lr, **rr}))
                    left_matched[i] = True
                    right_matched[j] = True
        if join_type == 'INNER':
            return [m[2] for m in matches]
        elif join_type == 'LEFT':
            right_cols = set(right_rows[0].keys()) if right_rows else set()
            result = []
            for i, lr in enumerate(left_rows):
                row_matches = [m for m in matches if m[0] == i]
                if row_matches:
                    for m in row_matches:
                        result.append(m[2])
                else:
                    row = lr.copy()
                    for col in right_cols:
                        if col not in row:
                            row[col] = None
                    result.append(row)
            return result
        elif join_type == 'RIGHT':
            left_cols = set(left_rows[0].keys()) if left_rows else set()
            result = []
            for j, rr in enumerate(right_rows):
                row_matches = [m for m in matches if m[1] == j]
                if row_matches:
                    for m in row_matches:
                        result.append(m[2])
                else:
                    row = {}
                    for col in left_cols:
                        row[col] = None
                    row.update(rr)
                    result.append(row)
            return result
        elif join_type == 'FULL':
            left_cols = set(left_rows[0].keys()) if left_rows else set()
            right_cols = set(right_rows[0].keys()) if right_rows else set()
            result = []
            result.extend([m[2] for m in matches])
            left_matched_indices = {m[0] for m in matches}
            for i, lr in enumerate(left_rows):
                if i not in left_matched_indices:
                    row = lr.copy()
                    for col in right_cols:
                        if col not in row:
                            row[col] = None
                    result.append(row)
            right_matched_indices = {m[1] for m in matches}
            for j, rr in enumerate(right_rows):
                if j not in right_matched_indices:
                    row = {}
                    for col in left_cols:
                        row[col] = None
                    row.update(rr)
                    result.append(row)
            return result
        else:
            raise ValueError(f"Unsupported join type: {join_type}")
        
        
    def _create_index(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_create_index(sql)
            index_name = parsed["index_name"]
            table_name = parsed["table_name"]
            column = parsed["column"]
            schema = self.storage.get_schema(table_name)
            if not schema:
                return f"Error: Table '{table_name}' does not exist"
            column_names = [c["name"] for c in schema]
            if column not in column_names:
                return f"Error: Column '{column}' does not exist in table '{table_name}'"
            if index_name in self.storage.data['indexes']:
                return f"Error: Index '{index_name}' already exists"
            rows = self.storage.get_table_data(table_name)
            index_map = {}
            for idx, row in enumerate(rows):
                val = row.get(column)
                if val is not None:
                    index_map.setdefault(val, []).append(idx)
            self.storage.data['indexes'][index_name] = {
                "table": table_name,
                "column": column,
                "map": index_map
            }
            self.storage.save()
            logger.info(f"Created index '{index_name}' on {table_name}({column}) with {len(index_map)} keys")
            return f"Index '{index_name}' created successfully"
        except Exception as e:
            logger.error(f"CREATE INDEX failed: {e}")
            return f"Error: {e}"

    def _drop_index(self, sql: str) -> str:
        try:
            index_name = SQLParser.parse_drop_index(sql)
            if index_name not in self.storage.data['indexes']:
                return f"Error: Index '{index_name}' does not exist"
            del self.storage.data['indexes'][index_name]
            self.storage.save()
            logger.info(f"Dropped index '{index_name}'")
            return f"Index '{index_name}' dropped successfully"
        except Exception as e:
            logger.error(f"DROP INDEX failed: {e}")
            return f"Error: {e}"

    def _find_index_for_condition(self, table: str, condition_str: str) -> Optional[Tuple[str, Any]]:
        import re
        match = re.match(r'^\s*(\w+)\s*=\s*(.+)$', condition_str)
        if not match:
            return None
        column = match.group(1)
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
        for idx_name, idx_info in self.storage.data['indexes'].items():
            if idx_info["table"] == table and idx_info["column"] == column:
                return (idx_name, value)
        return None
    
        
    def _alter_table(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_alter_table(sql)
            table_name = parsed["table_name"]
            schema = self.storage.get_schema(table_name)
            if not schema:
                return f"Error: Table '{table_name}' does not exist"
            operation = parsed["operation"]

            if operation == "ADD":
                column_def = parsed["column_def"]
                existing_cols = [c["name"] for c in schema]
                if column_def["name"] in existing_cols:
                    return f"Error: Column '{column_def['name']}' already exists in table '{table_name}'"
                schema.append(column_def)
                self.storage.set_schema(table_name, schema)
                rows = self.storage.get_table_data(table_name)
                default_val = column_def.get("default")
                for row in rows:
                    row[column_def["name"]] = default_val
                self.storage.set_table_data(table_name, rows)
                logger.info(f"Added column '{column_def['name']}' to table '{table_name}'")
                return f"Column '{column_def['name']}' added to table '{table_name}'"

            elif operation == "DROP":
                col_name = parsed["column_name"]
                if col_name not in [c["name"] for c in schema]:
                    return f"Error: Column '{col_name}' does not exist in table '{table_name}'"
                new_schema = [c for c in schema if c["name"] != col_name]
                self.storage.set_schema(table_name, new_schema)
                rows = self.storage.get_table_data(table_name)
                for row in rows:
                    if col_name in row:
                        del row[col_name]
                self.storage.set_table_data(table_name, rows)
                indexes_to_drop = [idx for idx, info in self.storage.data['indexes'].items()
                                if info["table"] == table_name and info["column"] == col_name]
                for idx in indexes_to_drop:
                    del self.storage.data['indexes'][idx]
                    logger.info(f"Dropped index '{idx}' because column '{col_name}' was removed")
                self.storage.save()
                logger.info(f"Dropped column '{col_name}' from table '{table_name}'")
                return f"Column '{col_name}' dropped from table '{table_name}'"

            elif operation == "RENAME_COLUMN":
                old_name = parsed["old_column"]
                new_name = parsed["new_column"]
                if old_name not in [c["name"] for c in schema]:
                    return f"Error: Column '{old_name}' does not exist in table '{table_name}'"
                if new_name in [c["name"] for c in schema]:
                    return f"Error: Column '{new_name}' already exists in table '{table_name}'"
                for col in schema:
                    if col["name"] == old_name:
                        col["name"] = new_name
                        break
                self.storage.set_schema(table_name, schema)
                rows = self.storage.get_table_data(table_name)
                for row in rows:
                    if old_name in row:
                        row[new_name] = row.pop(old_name)
                self.storage.set_table_data(table_name, rows)
                for info in self.storage.data['indexes'].values():
                    if info["table"] == table_name and info["column"] == old_name:
                        info["column"] = new_name
                self.storage.save()
                logger.info(f"Renamed column '{old_name}' to '{new_name}' in table '{table_name}'")
                return f"Column '{old_name}' renamed to '{new_name}'"

            elif operation == "RENAME_TABLE":
                new_table_name = parsed["new_table_name"]
                if self.storage.get_schema(new_table_name):
                    return f"Error: Table '{new_table_name}' already exists"
                self.storage.data["schemas"][new_table_name] = self.storage.data["schemas"].pop(table_name)
                self.storage.data["tables"][new_table_name] = self.storage.data["tables"].pop(table_name)
                if table_name in self.storage.data['foreign_keys']:
                    self.storage.data['foreign_keys'][new_table_name] = self.storage.data['foreign_keys'].pop(table_name)
                if table_name in self.storage.data['check_constraints']:
                    self.storage.data['check_constraints'][new_table_name] = self.storage.data['check_constraints'].pop(table_name)
                for info in self.storage.data['indexes'].values():
                    if info["table"] == table_name:
                        info["table"] = new_table_name
                self.storage.save()
                logger.info(f"Renamed table '{table_name}' to '{new_table_name}'")
                return f"Table '{table_name}' renamed to '{new_table_name}'"

            elif operation == "ADD_CHECK":
                check_name = parsed["check_name"]
                expr = parsed["expression"]
                if 'check_constraints' not in self.storage.data:
                    self.storage.data['check_constraints'] = {}
                checks = self.storage.data['check_constraints'].get(table_name, [])
                checks.append(expr)
                self.storage.data['check_constraints'][table_name] = checks
                self.storage.save()
                return f"Check constraint '{check_name}' added to table '{table_name}'"

            elif operation == "SET_DEFAULT":
                col_name = parsed["column"]
                default_val = parsed["default_value"]
                for col in schema:
                    if col["name"] == col_name:
                        col["default"] = default_val
                        self.storage.set_schema(table_name, schema)
                        self.storage.save()
                        return f"Default value set for column '{col_name}'"
                return f"Error: Column '{col_name}' not found in table '{table_name}'"

            elif operation == "DROP_DEFAULT":
                col_name = parsed["column"]
                for col in schema:
                    if col["name"] == col_name:
                        col["default"] = None
                        self.storage.set_schema(table_name, schema)
                        self.storage.save()
                        return f"Default value dropped for column '{col_name}'"
                return f"Error: Column '{col_name}' not found in table '{table_name}'"

            else:
                return f"Error: Unknown ALTER TABLE operation '{operation}'"
        except Exception as e:
            logger.error(f"ALTER TABLE failed: {e}")
            return f"Error: {e}"

    def _check_parent_exists(self, child_table: str, col: str, value: Any) -> bool:
        """Check if value exists in the referenced parent column."""
        foreign_keys = self.storage.data['foreign_keys'].get(child_table, [])
        for fk in foreign_keys:
            if fk["column"] == col:
                parent_table = fk["ref_table"]
                parent_col = fk["ref_column"]
                parent_rows = self.storage.get_table_data(parent_table)
                for row in parent_rows:
                    if row.get(parent_col) == value:
                        return True
                return False
        return True  # no foreign key for this column

    def _handle_child_references(self, parent_table: str, parent_col: str, old_value: Any, new_value: Any = None, is_delete: bool = False):
        """When parent row is updated or deleted, cascade changes to child tables."""
        for child_table, fk_list in self.storage.data['foreign_keys'].items():
            for fk in fk_list:
                if fk["ref_table"] == parent_table and fk["ref_column"] == parent_col:
                    child_col = fk["column"]
                    action = fk["on_delete"] if is_delete else fk["on_update"]
                    child_rows = self.storage.get_table_data(child_table)
                    updated = False
                    if action == "CASCADE":
                        new_child_rows = []
                        for row in child_rows:
                            if row.get(child_col) == old_value:
                                if not is_delete:
                                    row[child_col] = new_value
                                    new_child_rows.append(row)
                                # else: delete row – do not add
                            else:
                                new_child_rows.append(row)
                        self.storage.set_table_data(child_table, new_child_rows)
                        updated = True
                    elif action == "SET NULL":
                        for row in child_rows:
                            if row.get(child_col) == old_value:
                                row[child_col] = None
                        self.storage.set_table_data(child_table, child_rows)
                        updated = True
                    elif action in ("RESTRICT", "NO ACTION"):
                        for row in child_rows:
                            if row.get(child_col) == old_value:
                                raise Exception(f"Referenced row exists in {child_table}.{child_col}")
                    if updated:
                        self.storage.save()
                            
    def _check_constraints(self, table_name: str, row: Dict) -> Optional[str]:
        checks = self.storage.data['check_constraints'].get(table_name, [])
        for expr in checks:
            # Evaluate expression using existing _evaluate_condition (which uses eval with row as locals)
            if not self._evaluate_condition(row, expr):
                return f"Check constraint violated: {expr}"
        return None
    
    
    def _create_view(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_create_view(sql)
            view_name = parsed["view_name"]
            view_query = parsed["query"]
            if view_name in self.storage.data.get('views', {}):
                return f"Error: View '{view_name}' already exists"
            self.storage.data['views'][view_name] = view_query
            self.storage.save()
            logger.info(f"Created view '{view_name}'")
            return f"View '{view_name}' created successfully"
        except Exception as e:
            logger.error(f"CREATE VIEW failed: {e}")
            return f"Error: {e}"

    def _drop_view(self, sql: str) -> str:
        try:
            view_name = SQLParser.parse_drop_view(sql)
            if view_name not in self.storage.data.get('views', {}):
                return f"Error: View '{view_name}' does not exist"
            del self.storage.data['views'][view_name]
            self.storage.save()
            logger.info(f"Dropped view '{view_name}'")
            return f"View '{view_name}' dropped successfully"
        except Exception as e:
            logger.error(f"DROP VIEW failed: {e}")
            return f"Error: {e}"
        
        
        
        
    def _create_procedure(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_create_procedure(sql)
            proc_name = parsed["proc_name"]
            params = parsed["params"]
            body = parsed["body"]
            if proc_name in self.storage.data['procedures']:
                return f"Error: Procedure '{proc_name}' already exists"
            self.storage.data['procedures'][proc_name] = {"params": params, "body": body}
            self.storage.save()
            logger.info(f"Created procedure '{proc_name}'")
            return f"Procedure '{proc_name}' created successfully"
        except Exception as e:
            logger.error(f"CREATE PROCEDURE failed: {e}")
            return f"Error: {e}"

    def _drop_procedure(self, sql: str) -> str:
        try:
            proc_name = SQLParser.parse_drop_procedure(sql)
            if proc_name not in self.storage.data['procedures']:
                return f"Error: Procedure '{proc_name}' does not exist"
            del self.storage.data['procedures'][proc_name]
            self.storage.save()
            logger.info(f"Dropped procedure '{proc_name}'")
            return f"Procedure '{proc_name}' dropped successfully"
        except Exception as e:
            logger.error(f"DROP PROCEDURE failed: {e}")
            return f"Error: {e}"

    def _call_procedure(self, sql: str) -> APIResponse:
        try:
            parsed = SQLParser.parse_call(sql)
            proc_name = parsed["proc_name"]
            args = parsed["args"]
            if proc_name not in self.storage.data['procedures']:
                return ResponseFactory.error(f"Procedure '{proc_name}' does not exist", status=400)
            proc_def = self.storage.data['procedures'][proc_name]
            params = proc_def["params"]
            body = proc_def["body"]
            if len(args) != len(params):
                return ResponseFactory.error(f"Procedure '{proc_name}' expects {len(params)} arguments, got {len(args)}", status=400)
            substituted = body
            for i, arg in enumerate(args, start=1):
                if isinstance(arg, str):
                    arg_repr = f"'{arg}'"
                elif arg is None:
                    arg_repr = "NULL"
                else:
                    arg_repr = str(arg)
                substituted = re.sub(rf'\${i}\b', arg_repr, substituted)
            # Execute the raw SQL command (no prefix)
            return self.execute(substituted)
        except Exception as e:
            logger.error(f"CALL failed: {e}")
            return ResponseFactory.error(str(e), status=500)
        
            
            
    def _create_trigger(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_create_trigger(sql)
            self.storage.data['triggers'].append(parsed)
            self.storage.save()
            logger.info(f"Created trigger '{parsed['name']}'")
            return f"Trigger '{parsed['name']}' created successfully"
        except Exception as e:
            logger.error(f"CREATE TRIGGER failed: {e}")
            return f"Error: {e}"

    def _drop_trigger(self, sql: str) -> str:
        try:
            name = SQLParser.parse_drop_trigger(sql)
            triggers = self.storage.data['triggers']
            for i, t in enumerate(triggers):
                if t['name'] == name:
                    del triggers[i]
                    self.storage.save()
                    logger.info(f"Dropped trigger '{name}'")
                    return f"Trigger '{name}' dropped successfully"
            return f"Error: Trigger '{name}' does not exist"
        except Exception as e:
            logger.error(f"DROP TRIGGER failed: {e}")
            return f"Error: {e}"

    def _fire_triggers(self, table: str, timing: str, event: str, old_row: Dict = None, new_row: Dict = None):
        for trigger in self.storage.data['triggers']:
            if trigger['table'] == table and trigger['timing'] == timing and trigger['event'] == event:
                self._execute_trigger(trigger, old_row, new_row)

    def _execute_trigger(self, trigger_def: dict, old_row: dict = None, new_row: dict = None):
        statement = trigger_def['statement']
        if old_row:
            for col, val in old_row.items():
                statement = re.sub(rf'OLD\.{col}\b', repr(val), statement)
        if new_row:
            for col, val in new_row.items():
                statement = re.sub(rf'NEW\.{col}\b', repr(val), statement)
        # Do NOT add SQL prefix
        logger.info(f"Executing trigger: {statement}")
        result = self.execute(statement)
        if not result.success:
            logger.error(f"Trigger failed: {result.message}")

    def _insert_from_nosql(self, sql: str) -> str:
        """
        Parses: INSERT INTO table (col1, col2, ...) SELECT col1, col2, ... FROM NOSQL (nosql_select_command)
        Optimized for bulk insert: collects all rows, then appends once.
        """
        try:
            # Pattern: INSERT INTO table (cols) SELECT cols FROM NOSQL (subquery)
            pattern = r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)\s+SELECT\s+(.+)\s+FROM\s+NOSQL\s+\((.+)\)"
            match = re.match(pattern, sql, re.IGNORECASE | re.DOTALL)
            if not match:
                return "Error: Invalid INSERT ... SELECT FROM NOSQL syntax"
            table_name = match.group(1)
            target_cols_str = match.group(2).strip()
            select_cols_str = match.group(3).strip()
            nosql_subquery = match.group(4).strip()

            target_cols = [c.strip() for c in target_cols_str.split(',')]
            select_cols = [c.strip() for c in select_cols_str.split(',')]

            if len(target_cols) != len(select_cols):
                return f"Error: Number of target columns ({len(target_cols)}) does not match number of selected columns ({len(select_cols)})"

            # Execute NoSQL subquery once
            if not self.nosql_engine:
                return "Error: NoSQL engine not available"
            nosql_resp = self.nosql_engine.execute(nosql_subquery)
            if not nosql_resp.success:
                return f"Error in NoSQL subquery: {nosql_resp.message}"

            # Extract documents from NoSQL result
            documents = []
            if nosql_resp.data.type == "table":
                documents = nosql_resp.data.content
            elif nosql_resp.data.type == "json":
                if isinstance(nosql_resp.data.content, list):
                    documents = nosql_resp.data.content
                else:
                    documents = [nosql_resp.data.content]
            else:
                return "Error: NoSQL subquery did not return a list of documents"

            # Get target table schema and existing rows
            schema = self.storage.get_schema(table_name)
            if not schema:
                return f"Error: Table '{table_name}' does not exist"

            existing_rows = self.storage.get_table_data(table_name)
            new_rows = []
            inserted_count = 0

            # For each document, build a row dictionary (value validation, constraints, triggers)
            for doc in documents:
                # Build value dict from document using selected column paths
                value_dict = {}
                for tcol, scol in zip(target_cols, select_cols):
                    val = self._get_nested_nosql(doc, scol)
                    value_dict[tcol] = val

                # Build the row (replicating _insert logic without writing to storage yet)
                row = {}
                for col_def in schema:
                    col_name = col_def["name"]
                    col_type = col_def["type"]
                    constraints = col_def["constraints"]
                    val = value_dict.get(col_name, None)

                    # Auto‑increment handling
                    if col_def.get('auto_increment'):
                        if val is None:
                            counter = self.storage.data['auto_increment'].get(table_name, 0) + 1
                            self.storage.data['auto_increment'][table_name] = counter
                            typed_val = counter
                        else:
                            try:
                                user_val = int(val)
                            except (ValueError, TypeError):
                                return f"Error: Invalid integer for auto‑increment column '{col_name}'"
                            current = self.storage.data['auto_increment'].get(table_name, 0)
                            if user_val > current:
                                self.storage.data['auto_increment'][table_name] = user_val
                            typed_val = user_val
                    else:
                        if val is None and col_def.get("default") is not None:
                            default_val = col_def["default"]
                            try:
                                typed_val = DataType.validate(default_val, col_type)
                            except:
                                typed_val = default_val
                        else:
                            try:
                                typed_val = DataType.validate(val, col_type) if val is not None else None
                            except ValueError as e:
                                return f"Error in column '{col_name}': {e}"

                    # Constraints
                    if "NOT NULL" in constraints and typed_val is None:
                        return f"Error: Column '{col_name}' cannot be NULL"
                    if "PRIMARY KEY" in constraints:
                        try:
                            # Check against existing_rows + already built new_rows
                            SQLConstraint.check_primary_key(typed_val, col_name, existing_rows + new_rows, len(existing_rows) + len(new_rows))
                        except ValueError as e:
                            return f"Error: {e}"
                    if "UNIQUE" in constraints and "PRIMARY KEY" not in constraints:
                        try:
                            SQLConstraint.check_unique(typed_val, col_name, existing_rows + new_rows, len(existing_rows) + len(new_rows))
                        except ValueError as e:
                            return f"Error: {e}"
                    row[col_name] = typed_val

                # CHECK constraints and foreign keys
                error = self._check_constraints(table_name, row)
                if error:
                    return error
                for col_name, col_val in row.items():
                    if not self._check_parent_exists(table_name, col_name, col_val):
                        return f"Error: Foreign key violation on {col_name}: value {col_val} not found"

                # Fire BEFORE INSERT trigger (per row)
                self._fire_triggers(table_name, "BEFORE", "INSERT", new_row=row)

                new_rows.append(row)
                inserted_count += 1

            # Bulk append all new rows
            existing_rows.extend(new_rows)
            self.storage.set_table_data(table_name, existing_rows)
            self.storage.save()

            # Fire AFTER INSERT triggers for each inserted row
            for row in new_rows:
                self._fire_triggers(table_name, "AFTER", "INSERT", new_row=row)

            logger.info(f"Bulk inserted {inserted_count} row(s) into '{table_name}' from NoSQL")
            return f"Insert successful. {inserted_count} row(s) inserted."

        except Exception as e:
            logger.error(f"INSERT FROM NOSQL failed: {e}")
            return f"Error: {e}"

    def _get_nested_nosql(self, doc: Dict, path: str) -> Any:
        """Retrieve value from nested document using dot notation (e.g., 'address.city')."""
        parts = path.split('.')
        current = doc
        for p in parts:
            if isinstance(current, dict) and p in current:
                current = current[p]
            else:
                return None
        return current

    def _quote_value(self, val: Any) -> str:
        """Return SQL‑quoted representation of a value."""
        if val is None:
            return "NULL"
        if isinstance(val, str):
            return f"'{val}'"
        if isinstance(val, (int, float)):
            return str(val)
        if isinstance(val, dict):
            # Convert dict to JSON string (single‑quoted for SQL)
            return f"'{json.dumps(val)}'"
        return f"'{val}'"
    
        
    # ---------- Statistics and Schema ----------
    def get_stats(self) -> Dict[str, Any]:
        schemas = self.storage.data.get('schemas', {})
        tables = self.storage.data.get('tables', {})
        indexes = self.storage.data.get('indexes', {})
        views = self.storage.data.get('views', {})
        procedures = self.storage.data.get('procedures', {})
        triggers = self.storage.data.get('triggers', [])
        foreign_keys = self.storage.data.get('foreign_keys', {})
        check_constraints = self.storage.data.get('check_constraints', {})
        auto_increment = self.storage.data.get('auto_increment', {})

        table_stats = {}
        for table_name, rows in tables.items():
            schema = schemas.get(table_name, [])
            table_stats[table_name] = {
                "row_count": len(rows),
                "indexes": [idx for idx, info in indexes.items() if info.get("table") == table_name],
                "foreign_keys": len(foreign_keys.get(table_name, [])),
                "check_constraints": len(check_constraints.get(table_name, [])),
                "auto_increment_counter": auto_increment.get(table_name),
                "schema": schema   # full column definitions
            }

        view_list = list(views.keys())
        procedure_list = list(procedures.keys())
        trigger_list = [t["name"] for t in triggers]

        return {
            "tables": table_stats,
            "views": view_list,
            "procedures": procedure_list,
            "triggers": trigger_list,
            "total_tables": len(tables),
            "total_views": len(views),
            "total_procedures": len(procedures),
            "total_triggers": len(triggers)
        }

    def get_schema(self, table_name: str):
        """Return schema (columns) for a table, or None if table does not exist."""
        return self.storage.get_schema(table_name)
    
    
    def _execute_set_operation(self, sql: str, depth: int = 0) -> str:
        """
        Execute a set operation (UNION, INTERSECT, EXCEPT) and return JSON result.
        Handles simple top-level operation without parentheses.
        Also supports literal SELECTs without FROM.
        """
        sql_upper = sql.upper()
        depth_paren = 0
        op_pos = -1
        op_type = None
        for i, ch in enumerate(sql):
            if ch == '(':
                depth_paren += 1
            elif ch == ')':
                depth_paren -= 1
            elif depth_paren == 0:
                if sql_upper[i:i+5] == 'UNION':
                    op_pos = i
                    op_type = 'UNION'
                    break
                elif sql_upper[i:i+9] == 'INTERSECT':
                    op_pos = i
                    op_type = 'INTERSECT'
                    break
                elif sql_upper[i:i+6] == 'EXCEPT':
                    op_pos = i
                    op_type = 'EXCEPT'
                    break
        if op_pos == -1:
            return self._select(sql, depth)

        left_sql = sql[:op_pos].strip()
        right_sql = sql[op_pos + len(op_type):].strip()

        # Remove any leading 'SQL ' or 'NOSQL ' (just in case)
        for prefix in ('SQL ', 'NOSQL '):
            if left_sql.upper().startswith(prefix):
                left_sql = left_sql[len(prefix):].strip()
            if right_sql.upper().startswith(prefix):
                right_sql = right_sql[len(prefix):].strip()

        # Helper to execute a subquery (handles literal SELECTs)
        def execute_subquery(sub_sql: str) -> str:
            sub_upper = sub_sql.upper()
            # If it's a SELECT without FROM, treat as literal
            if sub_upper.startswith('SELECT') and ' FROM ' not in sub_upper:
                return self._execute_literal_select(sub_sql)
            # Otherwise use normal set-operation or SELECT
            if any(kw in sub_upper for kw in ('UNION', 'INTERSECT', 'EXCEPT')):
                return self._execute_set_operation(sub_sql, depth + 1)
            else:
                return self._select(sub_sql, depth + 1)

        left_result_str = execute_subquery(left_sql)
        right_result_str = execute_subquery(right_sql)

        if left_result_str.startswith("Error:") or right_result_str.startswith("Error:"):
            return f"Error in set operation: {left_result_str} {right_result_str}"

        left_rows = json.loads(left_result_str)
        right_rows = json.loads(right_result_str)

        if op_type == 'UNION':
            combined = left_rows + right_rows
            unique = []
            seen = set()
            for row in combined:
                row_tuple = tuple(sorted(row.items()))
                if row_tuple not in seen:
                    seen.add(row_tuple)
                    unique.append(row)
            rows = unique
        elif op_type == 'INTERSECT':
            right_set = {tuple(sorted(row.items())) for row in right_rows}
            rows = [row for row in left_rows if tuple(sorted(row.items())) in right_set]
        elif op_type == 'EXCEPT':
            right_set = {tuple(sorted(row.items())) for row in right_rows}
            rows = [row for row in left_rows if tuple(sorted(row.items())) not in right_set]
        else:
            return f"Error: Unsupported set operation {op_type}"

        return json.dumps(rows, indent=2, default=str)



    def _execute_literal_select(self, sql: str) -> str:
        """
        Execute a SELECT without FROM (e.g., SELECT 'Alice' AS name).
        Returns a JSON string with a single row.
        """
        sql = sql.strip()
        if not sql.upper().startswith("SELECT"):
            return "Error: Not a SELECT statement"
        rest = sql[6:].strip()
        parts = [p.strip() for p in rest.split(',')]
        row = {}
        for part in parts:
            alias = None
            if ' AS ' in part.upper():
                expr, alias = part.split(' AS ', 1)
                expr = expr.strip()
                alias = alias.strip()
            else:
                expr = part
                alias = None
            # Evaluate literal
            if expr.startswith("'") and expr.endswith("'"):
                val = expr[1:-1]
            elif expr.isdigit():
                val = int(expr)
            else:
                try:
                    val = float(expr)
                except ValueError:
                    val = expr
            if alias:
                row[alias] = val
            else:
                # Use expression as column name (fallback)
                row[expr] = val
        return json.dumps([row], indent=2)
        
    def _execute_with(self, sql: str, depth: int = 0) -> str:
        """
        Execute a WITH (CTE) query.
        Example: WITH cte AS (SELECT ...) SELECT ...
        Returns JSON string.
        """
        import re
        sql = sql.strip()
        if not sql.upper().startswith("WITH"):
            return self._select(sql, depth)
        
        # Remove 'WITH '
        rest = sql[4:].strip()
        
        # Find CTE name and its subquery
        m = re.match(r'(\w+)\s+AS\s*\(', rest, re.IGNORECASE)
        if not m:
            return "Error: Invalid WITH syntax"
        cte_name = m.group(1)
        start_paren = m.end() - 1   # position of '('
        
        # Find matching closing parenthesis
        paren_depth = 1
        i = start_paren + 1
        while i < len(rest) and paren_depth > 0:
            if rest[i] == '(':
                paren_depth += 1
            elif rest[i] == ')':
                paren_depth -= 1
            i += 1
        if paren_depth != 0:
            return "Error: Unmatched parentheses in CTE"
        
        subquery = rest[start_paren + 1 : i - 1].strip()
        main_query = rest[i:].strip()
        
        # Execute the CTE subquery (may itself be a CTE or normal SELECT)
        if subquery.upper().startswith("WITH"):
            sub_rows_str = self._execute_with(subquery, depth + 1)
        else:
            sub_rows_str = self._select(subquery, depth + 1)
        
        if sub_rows_str.startswith("Error:"):
            return sub_rows_str
        
        sub_rows = json.loads(sub_rows_str)
        
        # Save original table/schema data to restore later
        original_tables = self.storage.data['tables'].copy()
        original_schemas = self.storage.data['schemas'].copy()
        
        # Create a fake schema for the CTE
        if sub_rows and len(sub_rows) > 0:
            columns = [
                {"name": k, "type": "TEXT", "constraints": [], "default": None, "auto_increment": False}
                for k in sub_rows[0].keys()
            ]
        else:
            columns = []
        
        # Temporarily inject CTE as a table
        self.storage.data['schemas'][cte_name] = columns
        self.storage.data['tables'][cte_name] = sub_rows
        
        # Execute the main query (may contain the CTE name as a table)
        if main_query.upper().startswith("WITH"):
            result_str = self._execute_with(main_query, depth + 1)
        else:
            result_str = self._select(main_query, depth + 1)
        
        # Restore original state
        self.storage.data['tables'] = original_tables
        self.storage.data['schemas'] = original_schemas
        
        return result_str
    

    def _evaluate_exists(self, subquery_sql: str, outer_row: Dict, outer_table: str, depth: int = 0) -> bool:
        """
        Evaluate an EXISTS (or NOT EXISTS) subquery for a single outer row.
        Replaces occurrences of 'outer_table.column' with the literal value from outer_row,
        then executes the subquery and returns True if it returns at least one row.
        """
        # Clone the subquery string
        rewritten = subquery_sql
        # Replace each outer column reference with its value
        for col_name, col_val in outer_row.items():
            # Pattern: outer_table.col_name (e.g., users.id)
            pattern = rf'\b{outer_table}\.{col_name}\b'
            # Convert value to SQL literal
            if col_val is None:
                replacement = 'NULL'
            elif isinstance(col_val, str):
                replacement = f"'{col_val}'"
            else:
                replacement = str(col_val)
            rewritten = re.sub(pattern, replacement, rewritten, flags=re.IGNORECASE)
        # Execute the rewritten subquery
        try:
            result_str = self._select(rewritten, depth + 1)
            if result_str.startswith("Error:"):
                logger.error(f"EXISTS subquery failed: {result_str}")
                return False
            rows = json.loads(result_str)
            return len(rows) > 0
        except Exception as e:
            logger.error(f"Error evaluating EXISTS subquery: {e}")
            return False