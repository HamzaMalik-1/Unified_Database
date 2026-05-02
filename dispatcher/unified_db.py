# backend/dispatcher/unified_db.py
from catalog.database_catalog import DatabaseCatalog
from loguru import logger

class UnifiedDB:
    def __init__(self):
        self.catalog = DatabaseCatalog()
        # Automatically create and use default database if none exists
        if "default" not in self.catalog._databases:
            self.catalog.create_database("default")
        self.catalog.use_database("default")
        logger.info("UnifiedDB initialized with default database")

    def execute(self, command: str) -> str:
        cmd = command.strip()
        if cmd.upper().startswith("DATABASE "):
            return self._handle_database(cmd[9:].strip())
        elif cmd.upper().startswith("SQL "):
            try:
                engine = self.catalog.get_current_sql_engine()
                return engine.execute(cmd[4:].strip())
            except Exception as e:
                return f"SQL Error: {e}"
        elif cmd.upper().startswith("NOSQL "):
            try:
                engine = self.catalog.get_current_nosql_engine()
                return engine.execute(cmd[6:].strip())
            except Exception as e:
                return f"NoSQL Error: {e}"
        else:
            return "Unknown command. Use SQL ..., NOSQL ..., or DATABASE ..."

    def _handle_database(self, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: DATABASE CREATE <name> or DATABASE USE <name>"
        action, name = parts[0].upper(), parts[1]
        if action == "CREATE":
            return self.catalog.create_database(name)
        elif action == "USE":
            return self.catalog.use_database(name)
        else:
            return "Unknown DATABASE action. Use CREATE or USE."