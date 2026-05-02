from typing import Dict, List, Any

class SQLConstraint:
    @staticmethod
    def check_not_null(value: Any, column_name: str):
        if value is None:
            raise ValueError(f"Column '{column_name}' cannot be NULL")

    @staticmethod
    def check_unique(value: Any, column_name: str, existing_rows: List[Dict], position: int):
        for row in existing_rows:
            if row.get(column_name) == value:
                raise ValueError(f"Duplicate value '{value}' for UNIQUE column '{column_name}'")

    @staticmethod
    def check_primary_key(value: Any, column_name: str, existing_rows: List[Dict], position: int):
        SQLConstraint.check_not_null(value, column_name)
        SQLConstraint.check_unique(value, column_name, existing_rows, position)