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