# backend/parser/sql_parser.py
import re
from typing import Dict, Any, List, Tuple, Optional

class SQLParser:
   
    @staticmethod
    def parse_insert(sql: str) -> Dict[str, Any]:
        """
        Parses:
          INSERT INTO table_name VALUES (val1, val2, ...)
          INSERT INTO table_name (col1, col2, ...) VALUES (val1, val2, ...)
        Returns dict with:
          table_name, columns (list of column names, empty if all), values (list of raw strings)
        """
        sql = sql.strip()
        pattern = r"INSERT\s+INTO\s+(\w+)\s*(?:\(([^)]+)\))?\s+VALUES\s*\((.+)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid INSERT syntax. Expected: INSERT INTO table (columns) VALUES (values) or INSERT INTO table VALUES (values)")
        table_name = match.group(1)
        columns_str = match.group(2)
        values_str = match.group(3)
        columns = []
        if columns_str:
            columns = [c.strip() for c in columns_str.split(',')]
        values = []
        current = ""
        in_quote = False
        for ch in values_str:
            if ch == "'" and not in_quote:
                in_quote = True
                current += ch
            elif ch == "'" and in_quote:
                in_quote = False
                current += ch
            elif ch == ',' and not in_quote:
                values.append(current.strip())
                current = ""
            else:
                current += ch
        if current:
            values.append(current.strip())
        cleaned_values = []
        for v in values:
            v = v.strip()
            if v.startswith("'") and v.endswith("'"):
                cleaned_values.append(v[1:-1])
            elif v.upper() == "NULL":
                cleaned_values.append(None)
            elif v.isdigit():
                cleaned_values.append(int(v))
            else:
                try:
                    cleaned_values.append(float(v))
                except:
                    cleaned_values.append(v)
        return {
            "table_name": table_name,
            "columns": columns,
            "values": cleaned_values
        }

    @staticmethod
    def parse_select(sql: str) -> Dict[str, Any]:
        """Parse SELECT with support for joins, derived tables, subqueries, cross‑model NOSQL subqueries, and DISTINCT."""
        sql = sql.strip()
        original_sql_upper = sql.upper()

        # Find the position of the main FROM keyword (not inside parentheses)
        paren_depth = 0
        from_pos = -1
        for i, ch in enumerate(sql):
            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth -= 1
            elif paren_depth == 0 and sql[i:i+4].upper() == 'FROM':
                from_pos = i
                break
        if from_pos == -1:
            raise ValueError("SELECT missing FROM clause")

        select_part = sql[6:from_pos].strip()        # after "SELECT "

        # --- DISTINCT detection (robust) ---
        distinct = False
        # Use regex to match "DISTINCT" as a whole word, case-insensitive
        import re
        distinct_match = re.match(r'(?i)^DISTINCT\s+', select_part)
        if distinct_match:
            distinct = True
            select_part = select_part[distinct_match.end():].strip()

        after_from = sql[from_pos+4:].strip()        # everything after FROM

        # --- Find where the FROM clause ends (before WHERE, ORDER, GROUP, LIMIT, HAVING) ---
        clause_keywords = ['WHERE', 'ORDER BY', 'GROUP BY', 'LIMIT', 'HAVING']
        clause_positions = {}
        for kw in clause_keywords:
            depth = 0
            kw_len = len(kw)
            for j, ch in enumerate(after_from):
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                elif depth == 0 and after_from[j:j+kw_len].upper() == kw:
                    clause_positions[kw] = j
                    break
        from_end = len(after_from)
        if clause_positions:
            from_end = min(clause_positions.values())
        from_clause = after_from[:from_end].strip()
        rest = after_from[from_end:].strip() if from_end < len(after_from) else ""

        # --- Parse the FROM clause (supports derived table subqueries) ---
        # Tokenise the FROM clause, treating parenthesised subqueries as single tokens.
        tokens = []
        current = []
        in_paren = 0
        i = 0
        while i < len(from_clause):
            ch = from_clause[i]
            if ch == '(':
                if in_paren == 0:
                    # Start of a subquery token
                    if current:
                        tokens.append(''.join(current).strip())
                        current = []
                    paren_start = i
                    # Find matching closing parenthesis
                    depth = 1
                    j = i+1
                    while j < len(from_clause) and depth > 0:
                        if from_clause[j] == '(':
                            depth += 1
                        elif from_clause[j] == ')':
                            depth -= 1
                        j += 1
                    subquery = from_clause[paren_start:j].strip()
                    tokens.append(subquery)
                    i = j
                    continue
                else:
                    current.append(ch)
                    i += 1
            elif ch.isspace() and in_paren == 0:
                if current:
                    tokens.append(''.join(current).strip())
                    current = []
                i += 1
            else:
                current.append(ch)
                i += 1
        if current:
            tokens.append(''.join(current).strip())

        # Now parse tokens: first table (could be a subquery), then optional ALIAS, then joins
        if not tokens:
            raise ValueError("Invalid FROM clause")
        main_table = None
        # Check if first token is a subquery (starts with '(')
        if tokens[0].startswith('('):
            # It's a derived table: (SELECT ...) AS alias
            subquery_sql = tokens[0]
            # Look for alias after the subquery
            if len(tokens) > 1 and tokens[1].upper() == 'AS':
                if len(tokens) < 3:
                    raise ValueError("Missing alias after AS")
                main_table = tokens[2]
                tokens = tokens[3:]
            else:
                raise ValueError("Derived table requires alias (AS alias)")
        else:
            main_table = tokens[0]
            tokens = tokens[1:]

        # Parse joins (remaining tokens)
        joins = []
        i = 0
        while i < len(tokens):
            token = tokens[i].upper()
            if token == 'INNER':
                join_type = 'INNER'
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after INNER JOIN")
                join_table = tokens[i]
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'ON':
                    raise ValueError(f"Missing ON clause for JOIN {join_table}")
                i += 1
                condition_parts = []
                while i < len(tokens) and tokens[i].upper() not in ['INNER', 'LEFT', 'RIGHT', 'FULL', 'JOIN']:
                    condition_parts.append(tokens[i])
                    i += 1
                condition = ' '.join(condition_parts)
                joins.append(('INNER', join_table, condition))
            elif token == 'LEFT':
                join_type = 'LEFT'
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'JOIN':
                    raise ValueError("Expected JOIN after LEFT")
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after LEFT JOIN")
                join_table = tokens[i]
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'ON':
                    raise ValueError("Missing ON clause for LEFT JOIN")
                i += 1
                condition_parts = []
                while i < len(tokens) and tokens[i].upper() not in ['INNER', 'LEFT', 'RIGHT', 'FULL', 'JOIN']:
                    condition_parts.append(tokens[i])
                    i += 1
                condition = ' '.join(condition_parts)
                joins.append(('LEFT', join_table, condition))
            elif token == 'RIGHT':
                join_type = 'RIGHT'
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'JOIN':
                    raise ValueError("Expected JOIN after RIGHT")
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after RIGHT JOIN")
                join_table = tokens[i]
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'ON':
                    raise ValueError("Missing ON clause for RIGHT JOIN")
                i += 1
                condition_parts = []
                while i < len(tokens) and tokens[i].upper() not in ['INNER', 'LEFT', 'RIGHT', 'FULL', 'JOIN']:
                    condition_parts.append(tokens[i])
                    i += 1
                condition = ' '.join(condition_parts)
                joins.append(('RIGHT', join_table, condition))
            elif token == 'FULL':
                join_type = 'FULL'
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'JOIN':
                    raise ValueError("Expected JOIN after FULL")
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after FULL JOIN")
                join_table = tokens[i]
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'ON':
                    raise ValueError("Missing ON clause for FULL JOIN")
                i += 1
                condition_parts = []
                while i < len(tokens) and tokens[i].upper() not in ['INNER', 'LEFT', 'RIGHT', 'FULL', 'JOIN']:
                    condition_parts.append(tokens[i])
                    i += 1
                condition = ' '.join(condition_parts)
                joins.append(('FULL', join_table, condition))
            elif token == 'NATURAL':
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'JOIN':
                    raise ValueError("Expected JOIN after NATURAL")
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after NATURAL JOIN")
                join_table = tokens[i]
                i += 1
                joins.append(('NATURAL', join_table, None))
            elif token == 'CROSS':
                i += 1
                if i >= len(tokens) or tokens[i].upper() != 'JOIN':
                    raise ValueError("Expected JOIN after CROSS")
                i += 1
                if i >= len(tokens):
                    raise ValueError("Missing table after CROSS JOIN")
                join_table = tokens[i]
                i += 1
                joins.append(('CROSS', join_table, None))
            elif token == 'JOIN':
                join_type = 'INNER'
                join_table = tokens[i+1] if i+1 < len(tokens) else None
                if not join_table:
                    raise ValueError("Missing table after JOIN")
                i += 2
                if i >= len(tokens) or tokens[i].upper() != 'ON':
                    raise ValueError("Missing ON clause for JOIN")
                i += 1
                condition_parts = []
                while i < len(tokens) and tokens[i].upper() not in ['INNER', 'LEFT', 'RIGHT', 'FULL', 'JOIN']:
                    condition_parts.append(tokens[i])
                    i += 1
                condition = ' '.join(condition_parts)
                joins.append(('INNER', join_table, condition))
            else:
                raise ValueError(f"Unexpected token in FROM clause: {token}")

        # --- Parse SELECT columns (now without the "DISTINCT " prefix) ---
        if select_part == "*":
            columns = "*"
        else:
            columns = []
            current = []
            paren_depth = 0
            for ch in select_part:
                if ch == '(':
                    paren_depth += 1
                elif ch == ')':
                    paren_depth -= 1
                elif ch == ',' and paren_depth == 0:
                    columns.append(''.join(current).strip())
                    current = []
                    continue
                current.append(ch)
            if current:
                columns.append(''.join(current).strip())
            columns = [c for c in columns if c]

        # --- Parse WHERE, ORDER BY, LIMIT, GROUP BY, HAVING from the remaining part (rest) ---
        where_cond = None
        order_by = None
        limit = None
        group_by = None
        having = None

        where_match = re.search(r"\bWHERE\s+(.*?)(?:\s+ORDER\s+BY|\s+GROUP\s+BY|\s+LIMIT|$)", rest, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_cond = where_match.group(1).strip()
        order_match = re.search(r"\bORDER\s+BY\s+(\w+)\s*(ASC|DESC)?", rest, re.IGNORECASE)
        if order_match:
            col = order_match.group(1)
            direction = order_match.group(2).upper() if order_match.group(2) else "ASC"
            order_by = (col, direction)
        limit_match = re.search(r"\bLIMIT\s+(\d+)(?:\s*,\s*(\d+))?", rest, re.IGNORECASE)
        if limit_match:
            if limit_match.group(2) is not None:
                offset = int(limit_match.group(1))
                limit_val = int(limit_match.group(2))
                limit = (limit_val, offset)
            else:
                limit_val = int(limit_match.group(1))
                limit = (limit_val, None)
        group_match = re.search(r"\bGROUP\s+BY\s+(\w+)", rest, re.IGNORECASE)
        if group_match:
            group_by = group_match.group(1)
        if group_by:
            having_match = re.search(r"\bHAVING\s+(.*?)(?:\s+ORDER\s+BY|\s+LIMIT|$)", rest, re.IGNORECASE | re.DOTALL)
            if having_match:
                having = having_match.group(1).strip()

        return {
            "columns": columns,
            "main_table": main_table,
            "joins": joins,
            "where": where_cond,
            "order_by": order_by,
            "limit": limit,
            "group_by": group_by,
            "having": having,
            "distinct": distinct          # <-- CRITICAL: return the flag
        }
         
    @staticmethod
    def parse_update(sql: str) -> Dict[str, Any]:
        """
        Parses: UPDATE table_name SET col1 = val1, col2 = val2 [WHERE condition]
        """
        sql = sql.strip()
        pattern = r"UPDATE\s+(\w+)\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?$"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid UPDATE syntax. Expected: UPDATE table SET col=val, ... [WHERE condition]")
        table_name = match.group(1)
        set_part = match.group(2).strip()
        where_cond = match.group(3).strip() if match.group(3) else None
        assignments = {}
        parts = set_part.split(',')
        for part in parts:
            if '=' not in part:
                raise ValueError(f"Invalid SET clause: '{part}'")
            col, val_str = part.split('=', 1)
            col = col.strip()
            val_str = val_str.strip()
            if val_str.upper() == "NULL":
                assignments[col] = None
            elif val_str.startswith("'") and val_str.endswith("'"):
                assignments[col] = val_str[1:-1]
            elif val_str.isdigit():
                assignments[col] = int(val_str)
            else:
                try:
                    assignments[col] = float(val_str)
                except:
                    assignments[col] = val_str
        return {"table": table_name, "set": assignments, "where": where_cond}

    @staticmethod
    def parse_delete(sql: str) -> Dict[str, Any]:
        """
        Parses: DELETE FROM table_name [WHERE condition]
        """
        sql = sql.strip()
        pattern = r"DELETE\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?$"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DELETE syntax. Expected: DELETE FROM table [WHERE condition]")
        table_name = match.group(1)
        where_cond = match.group(2).strip() if match.group(2) else None
        return {"table": table_name, "where": where_cond}

    @staticmethod
    def parse_drop_table(sql: str) -> str:
        """Parses: DROP TABLE table_name, returns table name."""
        sql = sql.strip()
        pattern = r"DROP\s+TABLE\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP TABLE syntax. Expected: DROP TABLE table_name")
        return match.group(1)
    
    @staticmethod
    def extract_subqueries(where_clause: str) -> List[Tuple[str, str]]:
        """
        Find all subqueries in a WHERE clause.
        Returns list of (full_match, subquery_sql) for IN and EXISTS.
        """
        if not where_clause:
            return []
        subqueries = []
        in_pattern = r"(\w+)\s+IN\s*\(\s*(SELECT .+?)\s*\)"
        for match in re.finditer(in_pattern, where_clause, re.IGNORECASE):
            column = match.group(1)
            sub_sql = match.group(2)
            subqueries.append(("IN", column, sub_sql))
        exists_pattern = r"EXISTS\s*\(\s*(SELECT .+?)\s*\)"
        for match in re.finditer(exists_pattern, where_clause, re.IGNORECASE):
            sub_sql = match.group(1)
            subqueries.append(("EXISTS", None, sub_sql))
        return subqueries
    
    # backend/parser/sql_parser.py (add inside SQLParser class)

    @staticmethod
    def parse_create_index(sql: str) -> Dict[str, Any]:
        """
        Parses: CREATE INDEX index_name ON table_name (column_name)
        """
        pattern = r"CREATE\s+INDEX\s+(\w+)\s+ON\s+(\w+)\s*\(\s*(\w+)\s*\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE INDEX syntax. Expected: CREATE INDEX idx_name ON table_name (column)")
        return {
            "index_name": match.group(1),
            "table_name": match.group(2),
            "column": match.group(3)
        }


    @staticmethod
    def parse_drop_index(sql: str) -> str:
        """
        Parses: DROP INDEX index_name
        """
        pattern = r"DROP\s+INDEX\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP INDEX syntax. Expected: DROP INDEX index_name")
        return match.group(1)
    
    
    
    @staticmethod
    def parse_alter_table(sql: str) -> Dict[str, Any]:
        """
        Parses:
          ALTER TABLE table_name ADD column_name datatype [DEFAULT value] [constraints]
          ALTER TABLE table_name DROP COLUMN column_name
          ALTER TABLE table_name RENAME COLUMN old_name TO new_name
          ALTER TABLE table_name RENAME TO new_table_name
          ALTER TABLE table_name ADD CONSTRAINT check_name CHECK (expression)
          ALTER TABLE table_name ALTER COLUMN column_name SET DEFAULT value
          ALTER TABLE table_name ALTER COLUMN column_name DROP DEFAULT
        """
        sql = sql.strip()
        # RENAME TABLE
        rename_table_match = re.match(r"ALTER\s+TABLE\s+(\w+)\s+RENAME\s+TO\s+(\w+)", sql, re.IGNORECASE)
        if rename_table_match:
            return {
                "table_name": rename_table_match.group(1),
                "operation": "RENAME_TABLE",
                "new_table_name": rename_table_match.group(2)
            }
        # RENAME COLUMN
        rename_col_match = re.match(r"ALTER\s+TABLE\s+(\w+)\s+RENAME\s+COLUMN\s+(\w+)\s+TO\s+(\w+)", sql, re.IGNORECASE)
        if rename_col_match:
            return {
                "table_name": rename_col_match.group(1),
                "operation": "RENAME_COLUMN",
                "old_column": rename_col_match.group(2),
                "new_column": rename_col_match.group(3)
            }
        # DROP COLUMN
        drop_match = re.match(r"ALTER\s+TABLE\s+(\w+)\s+DROP\s+COLUMN\s+(\w+)", sql, re.IGNORECASE)
        if drop_match:
            return {
                "table_name": drop_match.group(1),
                "operation": "DROP",
                "column_name": drop_match.group(2)
            }
        # ADD CONSTRAINT CHECK (must come before ADD COLUMN)
        add_check_match = re.match(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+CHECK\s*\(\s*(.+?)\s*\)", sql, re.IGNORECASE)
        if add_check_match:
            return {
                "table_name": add_check_match.group(1),
                "operation": "ADD_CHECK",
                "check_name": add_check_match.group(2),
                "expression": add_check_match.group(3)
            }
        # ADD COLUMN
        add_match = re.match(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+(\w+)\s+(\w+)(?:\s+(.*))?", sql, re.IGNORECASE)
        if add_match:
            table_name = add_match.group(1)
            col_name = add_match.group(2)
            col_type = add_match.group(3).upper()
            rest = add_match.group(4) if add_match.group(4) else ""
            default_value = None
            constraints = []
            # Tokenise the rest (simple split)
            tokens = re.findall(r'[^\s]+', rest)
            i = 0
            while i < len(tokens):
                token = tokens[i].upper()
                if token == 'DEFAULT':
                    i += 1
                    if i >= len(tokens):
                        raise ValueError(f"DEFAULT value missing for column {col_name}")
                    default_value = tokens[i]
                    if default_value.startswith("'") and default_value.endswith("'"):
                        default_value = default_value[1:-1]
                    i += 1
                else:
                    constraints.append(token)
                    i += 1
            if "PRIMARY" in constraints and "KEY" in constraints:
                constraints.remove("PRIMARY")
                constraints.remove("KEY")
                constraints.append("PRIMARY KEY")
            if "NOT" in constraints and "NULL" in constraints:
                constraints.remove("NOT")
                constraints.remove("NULL")
                constraints.append("NOT NULL")
            column_def = {
                "name": col_name,
                "type": col_type,
                "constraints": constraints,
                "default": default_value
            }
            return {
                "table_name": table_name,
                "operation": "ADD",
                "column_def": column_def
            }
        # SET DEFAULT
        set_default_match = re.match(r"ALTER\s+TABLE\s+(\w+)\s+ALTER\s+COLUMN\s+(\w+)\s+SET\s+DEFAULT\s+(.+)", sql, re.IGNORECASE)
        if set_default_match:
            return {
                "table_name": set_default_match.group(1),
                "operation": "SET_DEFAULT",
                "column": set_default_match.group(2),
                "default_value": set_default_match.group(3).strip()
            }
        # DROP DEFAULT
        drop_default_match = re.match(r"ALTER\s+TABLE\s+(\w+)\s+ALTER\s+COLUMN\s+(\w+)\s+DROP\s+DEFAULT", sql, re.IGNORECASE)
        if drop_default_match:
            return {
                "table_name": drop_default_match.group(1),
                "operation": "DROP_DEFAULT",
                "column": drop_default_match.group(2)
            }
        raise ValueError("Invalid ALTER TABLE syntax")


    @staticmethod
    def parse_create_table(sql: str) -> Dict[str, Any]:
        pattern = r"CREATE\s+TABLE\s+(\w+)\s*\((.*)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CREATE TABLE syntax")
        table_name = match.group(1)
        columns_def = match.group(2).strip()

        columns = []
        foreign_keys = []
        checks = []

        # Split columns by commas, respecting parentheses
        col_parts = []
        current = []
        paren_depth = 0
        for ch in columns_def:
            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth -= 1
            elif ch == ',' and paren_depth == 0:
                col_parts.append(''.join(current).strip())
                current = []
                continue
            current.append(ch)
        if current:
            col_parts.append(''.join(current).strip())

        for col_str in col_parts:
            col_str = col_str.strip()
            if not col_str:
                continue
            # Tokenise (split by whitespace)
            tokens = re.findall(r'[^\s]+', col_str)
            if len(tokens) < 2:
                raise ValueError(f"Invalid column definition: '{col_str}'")
            col_name = tokens[0]
            # Check the second token for SERIAL or AUTO_INCREMENT
            raw_type = tokens[1].upper()
            auto_inc = raw_type in ('SERIAL', 'AUTO_INCREMENT')
            if auto_inc:
                col_type = 'INT'
            else:
                col_type = raw_type
            default_value = None
            constraints = []
            i = 2
            while i < len(tokens):
                token = tokens[i].upper()
                if token == 'DEFAULT':
                    i += 1
                    if i >= len(tokens):
                        raise ValueError(f"DEFAULT value missing for column {col_name}")
                    default_value = tokens[i]
                    if default_value.startswith("'") and default_value.endswith("'"):
                        default_value = default_value[1:-1]
                    i += 1
                else:
                    constraints.append(token)
                    i += 1
            # Clean up multi-word constraints
            if "PRIMARY" in constraints and "KEY" in constraints:
                constraints.remove("PRIMARY")
                constraints.remove("KEY")
                constraints.append("PRIMARY KEY")
            if "NOT" in constraints and "NULL" in constraints:
                constraints.remove("NOT")
                constraints.remove("NULL")
                constraints.append("NOT NULL")
            # For auto‑increment, ensure PRIMARY KEY if not already present
            if auto_inc and "PRIMARY KEY" not in constraints:
                constraints.append("PRIMARY KEY")

            columns.append({
                "name": col_name,
                "type": col_type,
                "constraints": constraints,
                "default": default_value,
                "auto_increment": auto_inc
            })

            # Inline foreign key detection
            ref_match = re.search(r'REFERENCES\s+(\w+)\s*\(\s*(\w+)\s*\)', col_str, re.IGNORECASE)
            if ref_match:
                foreign_keys.append({
                    "column": col_name,
                    "ref_table": ref_match.group(1),
                    "ref_column": ref_match.group(2),
                    "on_delete": 'RESTRICT',
                    "on_update": 'RESTRICT'
                })

        # Standalone FOREIGN KEY clauses
        fk_pattern = r"FOREIGN\s+KEY\s*\(\s*(\w+)\s*\)\s+REFERENCES\s+(\w+)\s*\(\s*(\w+)\s*\)(?:\s+ON\s+DELETE\s+(\w+))?(?:\s+ON\s+UPDATE\s+(\w+))?"
        for match in re.finditer(fk_pattern, columns_def, re.IGNORECASE):
            col = match.group(1)
            ref_table = match.group(2)
            ref_col = match.group(3)
            on_delete = match.group(4).upper() if match.group(4) else 'RESTRICT'
            on_update = match.group(5).upper() if match.group(5) else 'RESTRICT'
            foreign_keys.append({
                "column": col,
                "ref_table": ref_table,
                "ref_column": ref_col,
                "on_delete": on_delete,
                "on_update": on_update
            })

        # CHECK constraints
        check_pattern = r"CHECK\s*\(\s*(.+?)\s*\)"
        for match in re.finditer(check_pattern, columns_def, re.IGNORECASE):
            expr = match.group(1).strip()
            checks.append(expr)

        return {
            "table_name": table_name,
            "columns": columns,
            "foreign_keys": foreign_keys,
            "checks": checks
        }
    
    
    @staticmethod
    def _parse_foreign_key_clause(clause: str) -> Dict[str, Any]:
        """
        Parse a FOREIGN KEY clause:
            FOREIGN KEY (col) REFERENCES parent(pcol) [ON DELETE action] [ON UPDATE action]
        Returns dict with keys: column, ref_table, ref_column, on_delete, on_update
        """
        pattern = r"FOREIGN\s+KEY\s*\(\s*(\w+)\s*\)\s+REFERENCES\s+(\w+)\s*\(\s*(\w+)\s*\)(?:\s+ON\s+DELETE\s+(\w+))?(?:\s+ON\s+UPDATE\s+(\w+))?"
        match = re.match(pattern, clause, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid FOREIGN KEY syntax")
        col = match.group(1)
        ref_table = match.group(2)
        ref_col = match.group(3)
        on_delete = match.group(4).upper() if match.group(4) else 'RESTRICT'
        on_update = match.group(5).upper() if match.group(5) else 'RESTRICT'
        # Validate actions
        valid_actions = ['CASCADE', 'SET NULL', 'RESTRICT', 'NO ACTION']
        if on_delete not in valid_actions:
            raise ValueError(f"Invalid ON DELETE action: {on_delete}")
        if on_update not in valid_actions:
            raise ValueError(f"Invalid ON UPDATE action: {on_update}")
        return {
            "column": col,
            "ref_table": ref_table,
            "ref_column": ref_col,
            "on_delete": on_delete,
            "on_update": on_update
        }
        
            
    @staticmethod
    def parse_create_view(sql: str) -> Dict[str, Any]:
        pattern = r"CREATE\s+VIEW\s+(\w+)\s+AS\s+(SELECT\s+.+)"
        match = re.match(pattern, sql, re.IGNORECASE | re.DOTALL)
        if not match:
            raise ValueError("Invalid CREATE VIEW syntax")
        return {"view_name": match.group(1), "query": match.group(2).strip()}

    @staticmethod
    def parse_drop_view(sql: str) -> str:
        pattern = r"DROP\s+VIEW\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP VIEW syntax")
        return match.group(1)
        
    @staticmethod
    def parse_create_procedure(sql: str) -> Dict[str, Any]:
        """
        Parses: CREATE PROCEDURE name (param1, param2) AS sql_statement
        Returns dict: {"proc_name": str, "params": list, "body": str}
        """
        pattern = r"CREATE\s+PROCEDURE\s+(\w+)\s*\(([^)]*)\)\s+AS\s+(.+)"
        match = re.match(pattern, sql, re.IGNORECASE | re.DOTALL)
        if not match:
            raise ValueError("Invalid CREATE PROCEDURE syntax. Expected: CREATE PROCEDURE name (p1, p2) AS SELECT ...")
        proc_name = match.group(1)
        params_str = match.group(2).strip()
        body = match.group(3).strip()
        params = [p.strip() for p in params_str.split(',')] if params_str else []
        return {"proc_name": proc_name, "params": params, "body": body}

    @staticmethod
    def parse_drop_procedure(sql: str) -> str:
        """
        Parses: DROP PROCEDURE proc_name
        Returns: proc_name
        """
        pattern = r"DROP\s+PROCEDURE\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP PROCEDURE syntax. Expected: DROP PROCEDURE name")
        return match.group(1)

    @staticmethod
    def parse_call(sql: str) -> Dict[str, Any]:
        """
        Parses: CALL proc_name(value1, value2)
        Returns dict: {"proc_name": str, "args": list}
        """
        pattern = r"CALL\s+(\w+)\s*\(([^)]*)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid CALL syntax. Expected: CALL name(val1, val2)")
        proc_name = match.group(1)
        args_str = match.group(2).strip()
        args = []
        if args_str:
            # Parse arguments (simple comma separation, ignoring inner commas in strings)
            # For simplicity, we split by comma and strip.
            # In a full implementation, handle quotes and parentheses.
            args = [arg.strip() for arg in args_str.split(',')]
            # Convert values: numbers, strings, NULL
            cleaned = []
            for arg in args:
                if arg.upper() == 'NULL':
                    cleaned.append(None)
                elif arg.startswith("'") and arg.endswith("'"):
                    cleaned.append(arg[1:-1])
                elif arg.isdigit():
                    cleaned.append(int(arg))
                else:
                    try:
                        cleaned.append(float(arg))
                    except:
                        cleaned.append(arg)
            args = cleaned
        return {"proc_name": proc_name, "args": args}
           
    @staticmethod
    def parse_create_trigger(sql: str) -> Dict[str, Any]:
        pattern = r"CREATE\s+TRIGGER\s+(\w+)\s+(BEFORE|AFTER)\s+(INSERT|UPDATE|DELETE)\s+ON\s+(\w+)\s+FOR\s+EACH\s+ROW\s+BEGIN\s+(.+?)\s+END"
        match = re.match(pattern, sql, re.IGNORECASE | re.DOTALL)
        if not match:
            raise ValueError("Invalid CREATE TRIGGER syntax")
        return {
            "name": match.group(1),
            "timing": match.group(2).upper(),
            "event": match.group(3).upper(),
            "table": match.group(4),
            "statement": match.group(5).strip()
        }

    @staticmethod
    def parse_drop_trigger(sql: str) -> str:
        pattern = r"DROP\s+TRIGGER\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid DROP TRIGGER syntax")
        return match.group(1)
    
    