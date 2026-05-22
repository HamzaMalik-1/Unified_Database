# backend/dispatcher/unified_db.py
import time
from catalog.database_catalog import DatabaseCatalog
from loguru import logger
from utils.response import ResponseFactory, APIResponse
from typing import Dict,Any,List

class UnifiedDB:
    def __init__(self):
        self.catalog = DatabaseCatalog()
        # Automatically create and use default database if none exists
        if "default" not in self.catalog._databases:
            self.catalog.create_database("default")
        self.catalog.use_database("default")
        logger.info("UnifiedDB initialized with default database")

    def execute(self, command: str) -> APIResponse:
        start = time.perf_counter()
        cmd = command.strip()
        try:
            if cmd.upper().startswith("DATABASE "):
                result_msg = self._handle_database(cmd[9:].strip())
                elapsed = (time.perf_counter() - start) * 1000
                return ResponseFactory.text(result_msg, time_ms=elapsed)
            elif cmd.upper().startswith("SQL "):
                engine = self.catalog.get_current_sql_engine()
                resp = engine.execute(cmd[4:].strip())
                elapsed = (time.perf_counter() - start) * 1000
                if isinstance(resp, APIResponse):
                    resp.data.execution_time_ms = elapsed
                return resp
            elif cmd.upper().startswith("NOSQL "):
                engine = self.catalog.get_current_nosql_engine()
                resp = engine.execute(cmd[6:].strip())
                elapsed = (time.perf_counter() - start) * 1000
                if isinstance(resp, APIResponse):
                    resp.data.execution_time_ms = elapsed
                return resp
            else:
                elapsed = (time.perf_counter() - start) * 1000
                return ResponseFactory.error(
                    "Unknown command. Use SQL ..., NOSQL ..., or DATABASE ...",
                    status=400,
                    time_ms=elapsed
                )
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error(f"Execution failed: {e}")
            return ResponseFactory.error(str(e), status=500, time_ms=elapsed)

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
        
            
    # ---------- Statistics and Schema ----------
    def get_stats(self) -> Dict[str, Any]:
        sql_engine = self.catalog.get_current_sql_engine()
        nosql_engine = self.catalog.get_current_nosql_engine()
        return {
            "sql": sql_engine.get_stats(),
            "nosql": nosql_engine.get_stats()
        }

    def get_table_schema(self, table_name: str):
        sql_engine = self.catalog.get_current_sql_engine()
        return sql_engine.get_schema(table_name)
        
    def get_all_databases(self) -> List[str]:
        return self.catalog.get_all_database_names()

    def get_stats_for_database(self, db_name: str) -> Dict[str, Any]:
        return self.catalog.get_database_stats(db_name)
    
    def get_current_database(self) -> str:
        return self.catalog.get_current_database_name()