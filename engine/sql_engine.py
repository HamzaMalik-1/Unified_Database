# backend/engine/sql_engine.py
from storage.storage import Storage
from data_types.data_types import DataType
from constraints.sql_constraints import SQLConstraint
from parser.sql_parser import SQLParser
from loguru import logger

class SQLDatabase:
    def __init__(self, storage: Storage):
        self.storage = storage

    def execute(self, command: str) -> str:
        cmd_upper = command.strip().upper()
        if cmd_upper.startswith("CREATE TABLE"):
            return self._create_table(command)
        elif cmd_upper.startswith("INSERT INTO"):
            return self._insert(command)
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