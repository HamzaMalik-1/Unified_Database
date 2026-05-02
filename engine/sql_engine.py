# backend/engine/sql_engine.py
from storage.storage import Storage
from data_types.data_types import DataType
from constraints.sql_constraints import SQLConstraint
from parser.sql_parser import SQLParser
from loguru import logger
from typing import Dict,List,Tuple

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
        import re
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

            # Get rows
            rows = self.storage.get_table_data(table_name)
            if not rows:
                return "[]"

            # Apply WHERE (using improved evaluator)
            if where_cond:
                rows = [r for r in rows if self._evaluate_condition(r, where_cond)]

            # Aggregate handling
            if group_by:
                # GROUP BY with aggregates
                if columns_spec == "*":
                    # For SELECT *, we treat all columns as regular (no aggregates)
                    regular_cols = [c["name"] for c in self.storage.get_schema(table_name)]
                    agg_specs = {}
                else:
                    regular_cols, agg_specs = self._parse_aggregates(columns_spec)

                # Group rows by the group_by column
                groups = {}
                for row in rows:
                    key = row.get(group_by)
                    if key not in groups:
                        groups[key] = []
                    groups[key].append(row)

                # Build result rows
                result_rows = []
                for key, group_rows in groups.items():
                    row_result = {group_by: key}
                    # For regular columns, take the first non‑null value (assuming functional dependency)
                    for col in regular_cols:
                        val = next((r.get(col) for r in group_rows if r.get(col) is not None), None)
                        row_result[col] = val
                    # Compute aggregates
                    for alias, (func, src_col) in agg_specs.items():
                        if src_col == "count":
                            values = [1] * len(group_rows)  # COUNT(*)
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

                # Apply HAVING (after grouping)
                if having_cond:
                    rows = [r for r in rows if self._evaluate_condition(r, having_cond)]

            else:
                # No GROUP BY – if aggregates exist, compute over all rows
                if columns_spec != "*":
                    regular_cols, agg_specs = self._parse_aggregates(columns_spec)
                    if agg_specs:
                        # Single row result
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
                        # For regular columns, we cannot include them without GROUP BY – ignore
                        rows = [row_result]
                    else:
                        # No aggregates, keep rows as is
                        pass

            # Apply ORDER BY
            if order_by:
                col, direction = order_by
                reverse = (direction.upper() == "DESC")
                rows.sort(key=lambda r: r.get(col), reverse=reverse)

            # Apply LIMIT (with offset)
            if limit:
                limit_val, offset = limit
                start = offset if offset is not None else 0
                rows = rows[start:start+limit_val]

            # Select output columns (if columns_spec != "*" and no aggregates already handled)
            if columns_spec != "*" and not group_by and not agg_specs:
                # Plain column selection
                out_cols = columns_spec
                result_rows = []
                for row in rows:
                    new_row = {col: row.get(col) for col in out_cols}
                    result_rows.append(new_row)
                rows = result_rows
            elif columns_spec != "*" and group_by:
                # Already built with aliases, no extra filtering needed
                pass
            elif columns_spec == "*":
                # Keep all columns
                pass
            # else, rows already have correct shape

            # Return JSON
            import json
            return json.dumps(rows, indent=2, default=str)
        except Exception as e:
            logger.error(f"SELECT failed: {e}")
            return f"Error: {e}"