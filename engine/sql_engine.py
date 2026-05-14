# backend/engine/sql_engine.py
from storage.storage import Storage
from data_types.data_types import DataType
from constraints.sql_constraints import SQLConstraint
from parser.sql_parser import SQLParser
from loguru import logger
from typing import Dict,List,Tuple,Any
import re
import json

class SQLDatabase:
    def __init__(self, storage: Storage):
        self.storage = storage

    def execute(self, command: str) -> str:
        cmd_upper = command.strip().upper()
        if cmd_upper.startswith("CREATE TABLE"):
            return self._create_table(command)
        elif cmd_upper.startswith("INSERT INTO"):
            return self._insert(command)
        elif cmd_upper.startswith("SELECT"):
            return self._select(command)  
        elif cmd_upper.startswith("UPDATE"):
            return self._update(command)
        elif cmd_upper.startswith("DELETE FROM"):
            return self._delete(command)
        elif cmd_upper.startswith("DROP TABLE"):
            return self._drop_table(command)
        else:
            return f"Unsupported SQL command: {command}"

    def _create_table(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_create_table(sql)
            table_name = parsed["table_name"]
            columns = parsed["columns"]
            if self.storage.get_schema(table_name):
                return f"Error: Table '{table_name}' already exists"
            self.storage.set_schema(table_name, columns)
            self.storage.set_table_data(table_name, [])
            logger.info(f"Table '{table_name}' created with schema: {columns}")
            return f"Table '{table_name}' created successfully"
        except Exception as e:
            logger.error(f"CREATE TABLE failed: {e}")
            return f"Error: {e}"

    def _insert(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_insert(sql)
            table_name = parsed["table_name"]
            specified_cols = parsed["columns"]  # list of column names (may be empty)
            raw_values = parsed["values"]       # list of values (already typed: int, float, str, None)

            # Get schema
            schema = self.storage.get_schema(table_name)
            if not schema:
                return f"Error: Table '{table_name}' does not exist"
            existing_rows = self.storage.get_table_data(table_name)

            # Determine target columns and values
            if not specified_cols:
                # Use all columns in schema order
                target_cols = [col["name"] for col in schema]
                if len(raw_values) != len(target_cols):
                    return f"Error: Expected {len(target_cols)} values for all columns, got {len(raw_values)}"
                # Build a dict of column->value
                value_dict = dict(zip(target_cols, raw_values))
            else:
                # Specific columns provided
                # Check that all specified columns exist in schema
                schema_col_names = [col["name"] for col in schema]
                for col in specified_cols:
                    if col not in schema_col_names:
                        return f"Error: Column '{col}' does not exist in table '{table_name}'"
                if len(specified_cols) != len(raw_values):
                    return f"Error: Number of columns ({len(specified_cols)}) does not match number of values ({len(raw_values)})"
                # Build value dict for specified columns
                value_dict = dict(zip(specified_cols, raw_values))
                # For unspecified columns, we will set None (or raise if NOT NULL)
                target_cols = specified_cols

            # Prepare full row (ordered by schema columns)
            row = {}
            for col_def in schema:
                col_name = col_def["name"]
                col_type = col_def["type"]
                constraints = col_def["constraints"]

                # Get value for this column (may be None if not provided)
                val = value_dict.get(col_name, None)

                # Validate data type
                try:
                    typed_val = DataType.validate(val, col_type) if val is not None else None
                except ValueError as e:
                    return f"Error in column '{col_name}': {e}"

                # Check constraints
                # NOT NULL
                if "NOT NULL" in constraints and typed_val is None:
                    return f"Error: Column '{col_name}' cannot be NULL"
                # PRIMARY KEY (implies NOT NULL + UNIQUE)
                if "PRIMARY KEY" in constraints:
                    try:
                        SQLConstraint.check_primary_key(typed_val, col_name, existing_rows, len(existing_rows))
                    except ValueError as e:
                        return f"Error: {e}"
                # UNIQUE (if not already covered by PRIMARY KEY)
                if "UNIQUE" in constraints and "PRIMARY KEY" not in constraints:
                    try:
                        SQLConstraint.check_unique(typed_val, col_name, existing_rows, len(existing_rows))
                    except ValueError as e:
                        return f"Error: {e}"

                row[col_name] = typed_val

            # Append row
            existing_rows.append(row)
            self.storage.set_table_data(table_name, existing_rows)
            logger.info(f"Inserted row into '{table_name}': {row}")
            return "Insert successful"
        except Exception as e:
            logger.error(f"INSERT failed: {e}")
            return f"Error: {e}"
    
    # engine/sql_engine.py – inside SQLDatabase class

    def _evaluate_condition(self, row: Dict, condition_str: str) -> bool:
        """Evaluate WHERE/HAVING condition by converting SQL operators to Python."""
        if not condition_str:
            return True
        cond = condition_str.strip()
        # Convert SQL '=' to Python '==', but avoid changing '!=', '<=', '>=', '=='
        cond = re.sub(r'(?<![<>!=])=(?![=])', '==', cond)
        try:
            result = eval(cond, {"__builtins__": {}}, row)
            return bool(result)
        except Exception as e:
            logger.error(f"Condition evaluation failed: {cond} -> {e}")
            return False

    def _apply_aggregates(self, rows: List[Dict], group_col: str, agg_specs: Dict[str, str]) -> List[Dict]:
        """
        Simple grouping: group by group_col, compute aggregates for other columns.
        agg_specs: {"column": "function"} e.g., {"sales": "SUM", "count": "COUNT"}
        Returns list of dicts with group key and aggregate results.
        For now, we only support GROUP BY single column.
        """
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

    # engine/sql_engine.py – inside SQLDatabase class

    def _parse_aggregates(self, columns: List[str]) -> Tuple[List[str], Dict[str, Tuple[str, str]]]:
        """
        Parse column list, detect aggregate functions with optional alias.
        Returns:
            regular_columns: list of plain column names
            aggregates: dict {output_alias: (function, source_column)}
        Example: "SUM(amount) AS total" -> aggregates["total"] = ("SUM", "amount")
        """
        import re
        regular = []
        aggregates = {}
        # Patterns for aggregate functions (case‑insensitive)
        agg_patterns = {
            "COUNT": r"COUNT\(\s*(?:\*|\w+)\s*\)",
            "SUM": r"SUM\(\s*(\w+)\s*\)",
            "AVG": r"AVG\(\s*(\w+)\s*\)",
            "MIN": r"MIN\(\s*(\w+)\s*\)",
            "MAX": r"MAX\(\s*(\w+)\s*\)"
        }
        for col in columns:
            col_upper = col.upper()
            matched = False
            for func, pattern in agg_patterns.items():
                match = re.match(pattern, col, re.IGNORECASE)
                if match:
                    # Extract inner column (except COUNT)
                    if func == "COUNT":
                        inner = "count"  # placeholder
                    else:
                        inner = match.group(1)
                    # Check for alias: "AS alias"
                    alias_match = re.search(r"\s+AS\s+(\w+)", col, re.IGNORECASE)
                    if alias_match:
                        alias = alias_match.group(1)
                    else:
                        alias = f"{func}({inner})"
                    aggregates[alias] = (func, inner)
                    matched = True
                    break
            if not matched:
                regular.append(col)
        return regular, aggregates
    
    def _select(self, sql: str) -> str:
        try:
            parsed = SQLParser.parse_select(sql)
            table_name = parsed["table"]
            where_cond = parsed["where"]
            order_by = parsed["order_by"]
            limit = parsed["limit"]
            group_by = parsed["group_by"]
            having_cond = parsed["having"]
            columns_spec = parsed["columns"]

            rows = self.storage.get_table_data(table_name)
            if not rows:
                return "[]"

            # ========== Handle scalar subqueries in SELECT list ==========
            scalar_subqueries = []
            final_columns = []
            if columns_spec != "*":
                for col in columns_spec:
                    col = col.strip()
                    if col.startswith('('):
                        # Find matching closing parenthesis using depth counting
                        depth = 0
                        end_idx = -1
                        for i, ch in enumerate(col):
                            if ch == '(':
                                depth += 1
                            elif ch == ')':
                                depth -= 1
                                if depth == 0:
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
            # =================================================================

            # ========== Multi‑column IN subqueries ==========
            if where_cond and re.search(r"\(\s*[\w.]+\s*,\s*[\w.]+\s*\)\s+IN\s*\(", where_cond, re.IGNORECASE):
                pattern = r"\(\s*([\w.,\s]+)\s*\)\s+IN\s*\(\s*(SELECT.+?)\s*\)"
                match = re.search(pattern, where_cond, re.IGNORECASE | re.DOTALL)
                if match:
                    col_list_str = match.group(1).strip()
                    subquery_sql = match.group(2).strip()
                    outer_cols = [c.strip() for c in col_list_str.split(',')]
                    sub_rows = self._execute_select_to_rows(subquery_sql)
                    value_tuples = [tuple(r.values()) for r in sub_rows]
                    rows = [r for r in rows if tuple(r.get(c) for c in outer_cols) in value_tuples]
                    where_cond = None
            # =================================================================

            # ----- Uncorrelated IN subqueries -----
            if where_cond and "IN (SELECT" in where_cond.upper():
                pattern = r"(\w+)\s+IN\s*\(\s*(SELECT\s+.+?)\s*\)"
                match = re.search(pattern, where_cond, re.IGNORECASE | re.DOTALL)
                if match:
                    outer_column = match.group(1)
                    inner_sql = match.group(2).strip()
                    try:
                        inner_rows = self._execute_select_to_rows(inner_sql)
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
            # -------------------------------------------------------------

            # ----- Correlated EXISTS subqueries -----
            if where_cond and "EXISTS (SELECT" in where_cond.upper():
                pattern = r"EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+(\w+)\s+WHERE\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*\)"
                match = re.search(pattern, where_cond, re.IGNORECASE | re.DOTALL)
                if match:
                    inner_table = match.group(1)
                    inner_col = match.group(3)
                    outer_table = match.group(4)
                    outer_col = match.group(5)
                    inner_rows = self.storage.get_table_data(inner_table)
                    matching_values = {r.get(inner_col) for r in inner_rows if r.get(inner_col) is not None}
                    rows = [r for r in rows if r.get(outer_col) in matching_values]
                    where_cond = None
                else:
                    pattern_generic = r"EXISTS\s*\(\s*(SELECT\s+.+?)\s*\)"
                    match_gen = re.search(pattern_generic, where_cond, re.IGNORECASE | re.DOTALL)
                    if match_gen:
                        inner_sql = match_gen.group(1).strip()
                        filtered_rows = []
                        for row in rows:
                            modified_inner = inner_sql
                            for col_name, col_val in row.items():
                                modified_inner = re.sub(rf'{table_name}\.{col_name}', str(col_val), modified_inner, flags=re.IGNORECASE)
                            try:
                                if self._execute_select_to_rows(modified_inner):
                                    filtered_rows.append(row)
                            except Exception:
                                pass
                        rows = filtered_rows
                        where_cond = None

            # Apply WHERE
            if where_cond:
                rows = [r for r in rows if self._evaluate_condition(r, where_cond)]

            # ========== Attach scalar subquery results ==========
            if scalar_subqueries:
                for alias, sub_sql in scalar_subqueries:
                    scalar_val = self._evaluate_scalar_subquery(sub_sql)
                    for row in rows:
                        row[alias] = scalar_val
            # ====================================================

            # ----- Aggregate handling (unchanged) -----
            if group_by:
                if columns_spec == "*":
                    regular_cols = [c["name"] for c in self.storage.get_schema(table_name)]
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

            # Column selection – now using final_columns
            if final_columns != "*" and not group_by and not agg_specs:
                out_cols = final_columns
                result_rows = []
                for row in rows:
                    new_row = {col: row.get(col) for col in out_cols}
                    result_rows.append(new_row)
                rows = result_rows

            import json
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

            # Validate column existence and types
            schema_cols = {col["name"]: col for col in schema}
            for col in set_assignments.keys():
                if col not in schema_cols:
                    return f"Error: Column '{col}' does not exist in table '{table_name}'"

            updated_count = 0
            for row in rows:
                # Check WHERE condition (if any)
                if where_cond and not self._evaluate_condition(row, where_cond):
                    continue
                # Apply updates
                for col, val in set_assignments.items():
                    col_def = schema_cols[col]
                    # Validate data type
                    try:
                        typed_val = DataType.validate(val, col_def["type"]) if val is not None else None
                    except ValueError as e:
                        return f"Error in column '{col}': {e}"
                    # Check NOT NULL constraint if the column is being updated to NULL
                    if typed_val is None and "NOT NULL" in col_def["constraints"]:
                        return f"Error: Column '{col}' cannot be NULL"
                    row[col] = typed_val
                updated_count += 1
            # Persist changes
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

            if where_cond is None:
                # Delete all rows
                self.storage.set_table_data(table_name, [])
                original_count = len(rows)
                logger.info(f"Deleted all {original_count} row(s) from '{table_name}'")
                return f"Delete successful. {original_count} row(s) deleted."
            else:
                # Delete rows matching condition
                new_rows = []
                deleted_count = 0
                for row in rows:
                    if self._evaluate_condition(row, where_cond):
                        deleted_count += 1
                    else:
                        new_rows.append(row)
                self.storage.set_table_data(table_name, new_rows)
                logger.info(f"Deleted {deleted_count} row(s) from '{table_name}'")
                return f"Delete successful. {deleted_count} row(s) affected."
        except Exception as e:
            logger.error(f"DELETE failed: {e}")
            return f"Error: {e}"

    def _drop_table(self, sql: str) -> str:
        try:
            table_name = SQLParser.parse_drop_table(sql)
            if not self.storage.get_schema(table_name):
                return f"Error: Table '{table_name}' does not exist"
            # Remove schema and data
            del self.storage.data["schemas"][table_name]
            del self.storage.data["tables"][table_name]
            self.storage.save()
            logger.info(f"Dropped table '{table_name}'")
            return f"Table '{table_name}' dropped successfully"
        except Exception as e:
            logger.error(f"DROP TABLE failed: {e}")
            return f"Error: {e}"
        
    def _handle_subqueries(self, where_cond: str, outer_row: Dict = None) -> Tuple[str, bool]:
        """
        For uncorrelated IN: replace with list of values.
        For correlated EXISTS: evaluate per row (return new condition or bool).
        For simplicity, first only handle uncorrelated IN.
        """
        if not where_cond:
            return where_cond, True
        # Check for IN (SELECT ...)
        import re
        in_match = re.search(r"(\w+)\s+IN\s*\(\s*(SELECT .+?)\s*\)", where_cond, re.IGNORECASE | re.DOTALL)
        if in_match:
            column = in_match.group(1)
            sub_sql = in_match.group(2)
            # Execute subquery (uncorrelated)
            sub_result = self._select(sub_sql)  # but _select returns JSON string; need parsed list
            # We need to parse JSON result to extract values
            import json
            try:
                rows = json.loads(sub_result)
                values = [row.get(column) for row in rows if column in row]
                # Build new condition: column IN (val1, val2, ...)
                if not values:
                    new_cond = "1=0"  # false condition
                else:
                    vals_str = ','.join(str(v) for v in values)
                    new_cond = f"{column} IN ({vals_str})"
                # Replace original match with new_cond
                new_where = where_cond[:in_match.start()] + new_cond + where_cond[in_match.end():]
                return new_where, True
            except:
                pass
        return where_cond, True
    
    def _execute_select_to_rows(self, sql: str) -> List[Dict]:
        """Execute a SELECT query and return result as list of dicts (for internal use)."""
        result_str = self._select(sql)
        if result_str.startswith("Error:"):
            raise Exception(result_str)
        return json.loads(result_str)
    
    def _evaluate_scalar_subquery(self, subquery_sql: str) -> Any:
        """Execute a scalar subquery (returns one row, one column)."""
        rows = self._execute_select_to_rows(subquery_sql)
        if not rows:
            return None
        row = rows[0]
        # Return the first column value
        return list(row.values())[0] if row else None