# backend/dispatcher/unified_db.py
import time
from catalog.database_catalog import DatabaseCatalog
from loguru import logger
from utils.response import ResponseFactory, APIResponse
from typing import Dict, Any, List

class UnifiedDB:
    def __init__(self):
        self.catalog = DatabaseCatalog()
        # Automatically create and use default database if none exists
        if "default" not in self.catalog._databases:
            self.catalog.create_database("default")
        self.catalog.use_database("default")
        self.active_txn_mode = None  # None, "sql", "nosql", "cross"
        logger.info("UnifiedDB initialized with default database")

    # ---------- Transaction helpers ----------
    def _begin_transaction(self, mode: str = "cross"):
        """mode: 'sql', 'nosql', or 'cross'"""
        if self.active_txn_mode is not None:
            return ResponseFactory.error("Transaction already active", status=400)

        sql_engine = self.catalog.get_current_sql_engine()
        nosql_engine = self.catalog.get_current_nosql_engine()

        if mode == "sql":
            sql_engine.begin()
        elif mode == "nosql":
            nosql_engine.begin()
        else:  # cross
            sql_engine.begin()
            nosql_engine.begin()

        self.active_txn_mode = mode
        return ResponseFactory.text(f"{mode.upper()} transaction started")

    def _commit_sql(self):
        if self.active_txn_mode != "sql":
            return ResponseFactory.error("No active SQL transaction", status=400)
        sql_engine = self.catalog.get_current_sql_engine()
        try:
            sql_engine.commit()
            self.active_txn_mode = None
            return ResponseFactory.text("SQL transaction committed")
        except Exception as e:
            sql_engine.rollback()
            self.active_txn_mode = None
            return ResponseFactory.error(f"SQL commit failed: {e}", status=500)

    def _rollback_sql(self):
        if self.active_txn_mode != "sql":
            return ResponseFactory.error("No active SQL transaction", status=400)
        sql_engine = self.catalog.get_current_sql_engine()
        sql_engine.rollback()
        self.active_txn_mode = None
        return ResponseFactory.text("SQL transaction rolled back")

    def _commit_nosql(self):
        if self.active_txn_mode != "nosql":
            return ResponseFactory.error("No active NoSQL transaction", status=400)
        nosql_engine = self.catalog.get_current_nosql_engine()
        try:
            nosql_engine.commit()
            self.active_txn_mode = None
            return ResponseFactory.text("NoSQL transaction committed")
        except Exception as e:
            nosql_engine.rollback()
            self.active_txn_mode = None
            return ResponseFactory.error(f"NoSQL commit failed: {e}", status=500)

    def _rollback_nosql(self):
        if self.active_txn_mode != "nosql":
            return ResponseFactory.error("No active NoSQL transaction", status=400)
        nosql_engine = self.catalog.get_current_nosql_engine()
        nosql_engine.rollback()
        self.active_txn_mode = None
        return ResponseFactory.text("NoSQL transaction rolled back")

    def _commit_cross(self):
        if self.active_txn_mode != "cross":
            return ResponseFactory.error("No active cross‑model transaction", status=400)

        sql_engine = self.catalog.get_current_sql_engine()
        nosql_engine = self.catalog.get_current_nosql_engine()

        try:
            sql_engine.commit()
            nosql_engine.commit()
            self.active_txn_mode = None
            return ResponseFactory.text("Cross‑model transaction committed")
        except Exception as e:
            sql_engine.rollback()
            nosql_engine.rollback()
            self.active_txn_mode = None
            return ResponseFactory.error(f"Cross‑model commit failed: {e}", status=500)

    def _rollback_cross(self):
        if self.active_txn_mode != "cross":
            return ResponseFactory.error("No active cross‑model transaction", status=400)
        sql_engine = self.catalog.get_current_sql_engine()
        nosql_engine = self.catalog.get_current_nosql_engine()
        sql_engine.rollback()
        nosql_engine.rollback()
        self.active_txn_mode = None
        return ResponseFactory.text("Cross‑model transaction rolled back")

    # ---------- Main execute method with transaction handling ----------
    def execute(self, command: str) -> APIResponse:
        start = time.perf_counter()
        cmd = command.strip()
        cmd_upper = cmd.upper()

        # --- Transaction start commands (new syntax only) ---
        if cmd_upper == "SQL BEGIN TRANSACTION":
            return self._begin_transaction("sql")
        if cmd_upper == "NOSQL BEGIN TRANSACTION":
            return self._begin_transaction("nosql")
        if cmd_upper == "DATABASE BEGIN TRANSACTION":
            return self._begin_transaction("cross")

        # --- Transaction commit / rollback (per‑engine) ---
        if cmd_upper == "SQL COMMIT":
            return self._commit_sql()
        if cmd_upper == "SQL ROLLBACK":
            return self._rollback_sql()
        if cmd_upper == "NOSQL COMMIT":
            return self._commit_nosql()
        if cmd_upper == "NOSQL ROLLBACK":
            return self._rollback_nosql()
        if cmd_upper == "DATABASE COMMIT":
            return self._commit_cross()
        if cmd_upper == "DATABASE ROLLBACK":
            return self._rollback_cross()

        # --- Transaction command validation (if a transaction is active) ---
        if self.active_txn_mode is not None:
            # Determine the command's engine type
            if cmd_upper.startswith("SQL "):
                engine_type = "sql"
            elif cmd_upper.startswith("NOSQL "):
                engine_type = "nosql"
            elif cmd_upper.startswith("DATABASE "):
                engine_type = "database"
            else:
                elapsed = (time.perf_counter() - start) * 1000
                return ResponseFactory.error(
                    "Unknown command prefix. Use SQL, NOSQL, or DATABASE.",
                    status=400,
                    time_ms=elapsed
                )

            # Validate compatibility
            if self.active_txn_mode == "sql" and engine_type != "sql":
                elapsed = (time.perf_counter() - start) * 1000
                return ResponseFactory.error(
                    "Only SQL commands allowed inside this SQL transaction",
                    status=400,
                    time_ms=elapsed
                )
            if self.active_txn_mode == "nosql" and engine_type != "nosql":
                elapsed = (time.perf_counter() - start) * 1000
                return ResponseFactory.error(
                    "Only NoSQL commands allowed inside this NoSQL transaction",
                    status=400,
                    time_ms=elapsed
                )
            # Cross‑model transactions allow all commands (including DATABASE)

        # --- Original command routing (unchanged) ---
        try:
            if cmd_upper.startswith("DATABASE "):
                result_msg = self._handle_database(cmd[9:].strip())
                elapsed = (time.perf_counter() - start) * 1000
                return ResponseFactory.text(result_msg, time_ms=elapsed)
            elif cmd_upper.startswith("SQL "):
                engine = self.catalog.get_current_sql_engine()
                resp = engine.execute(cmd[4:].strip())
                elapsed = (time.perf_counter() - start) * 1000
                if isinstance(resp, APIResponse):
                    resp.data.execution_time_ms = elapsed
                return resp
            elif cmd_upper.startswith("NOSQL "):
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

    # ---------- Database management (unchanged) ----------
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