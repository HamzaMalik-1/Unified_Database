"""
constants.py
All static lookup tables and keyword mappings in one place.
Keeping these separate means you never have to hunt through logic code
to tweak a synonym or add a new date keyword.
"""

# ──────────────────────────────────────────────
#  Intent keyword sets
# ──────────────────────────────────────────────
SELECT_KEYWORDS   = {"show", "list", "find", "get", "retrieve", "select", "display", "fetch"}
INSERT_KEYWORDS   = {"add", "insert", "store", "create new", "record", "new"}
UPDATE_KEYWORDS   = {"update", "change", "modify", "set", "edit", "alter value"}
DELETE_KEYWORDS   = {"delete", "remove", "erase", "purge"}
DROP_KEYWORDS     = {"drop"}
CREATE_KEYWORDS   = {"create table", "create collection"}

# ──────────────────────────────────────────────
#  DDL trigger phrases (checked before general intent)
# ──────────────────────────────────────────────
DDL_PATTERNS = [
    "create index",
    "drop index",
    "create text index",
    "drop text index",
    "alter table",
    "create view",
    "drop view",
    "create procedure",
    "drop procedure",
    "create trigger",
    "drop trigger",
]

# ──────────────────────────────────────────────
#  Column synonym map  (natural word → actual column)
# ──────────────────────────────────────────────
COLUMN_SYNONYMS: dict[str, str] = {
    "sales":    "amount",
    "revenue":  "amount",
    "earnings": "amount",
    "turnover": "amount",
    "price":    "price",
    "cost":     "cost",
    "quantity": "quantity",
    "qty":      "quantity",
    "count":    "quantity",
    "number":   "quantity",
    "total":    "amount",
}

# ──────────────────────────────────────────────
#  Date / time keyword → SQL expression
# ──────────────────────────────────────────────
DATE_KEYWORDS: dict[str, str] = {
    "today":             "CURRENT_DATE",
    "yesterday":         "CURRENT_DATE - 1",
    "tomorrow":          "CURRENT_DATE + 1",
    "last week":         "CURRENT_DATE - 7",
    "last month":        "CURRENT_DATE - 30",
    "last year":         "CURRENT_DATE - 365",
    "this week":         "CURRENT_DATE - 7",
    "this month":        "CURRENT_DATE - 30",
    "this year":         "CURRENT_DATE - 365",
    "now":               "CURRENT_TIMESTAMP",
    "current time":      "CURRENT_TIMESTAMP",
    "current_timestamp": "CURRENT_TIMESTAMP",
    "right now":         "CURRENT_TIMESTAMP",
}

# ──────────────────────────────────────────────
#  Comparison operator aliases  (natural → SQL op)
# ──────────────────────────────────────────────
COMPARISON_ALIASES: dict[str, str] = {
    "greater than":         ">",
    "more than":            ">",
    "above":                ">",
    "over":                 ">",
    "older than":           ">",   # age context – caller decides col
    "less than":            "<",
    "below":                "<",
    "under":                "<",
    "younger than":         "<",
    "at least":             ">=",
    "greater than or equal":">=",
    "at most":              "<=",
    "less than or equal":   "<=",
    "not equal":            "!=",
    "not equals":           "!=",
    "different from":       "!=",
    "equals":               "=",
    "equal to":             "=",
    "is":                   "=",
}




# ──────────────────────────────────────────────
#  Aggregate function trigger words
# ──────────────────────────────────────────────
AGGREGATE_PATTERNS: list[tuple[str, str]] = [
    (r"count\s+of\s+(\w+)",    "COUNT"),
    (r"number\s+of\s+(\w+)",   "COUNT"),
    (r"total\s+(\w+)",         "SUM"),
    (r"sum\s+of\s+(\w+)",      "SUM"),
    (r"average\s+(\w+)",       "AVG"),
    (r"avg\s+(\w+)",           "AVG"),
    (r"mean\s+(\w+)",          "AVG"),
    (r"minimum\s+(\w+)",       "MIN"),
    (r"min\s+(\w+)",           "MIN"),
    (r"lowest\s+(\w+)",        "MIN"),
    (r"maximum\s+(\w+)",       "MAX"),
    (r"max\s+(\w+)",           "MAX"),
    (r"highest\s+(\w+)",       "MAX"),
]

# ──────────────────────────────────────────────
#  JOIN type aliases
# ──────────────────────────────────────────────
JOIN_ALIASES: dict[str, str] = {
    "left join":       "LEFT",
    "left outer join": "LEFT",
    "right join":      "RIGHT",
    "right outer join":"RIGHT",
    "full join":       "FULL",
    "full outer join": "FULL",
    "natural join":    "NATURAL",
    "cross join":      "CROSS",
    "inner join":      "INNER",
    "join":            "INNER",   # default
}

# ──────────────────────────────────────────────
#  NULL synonyms
# ──────────────────────────────────────────────
NULL_SYNONYMS   = {"missing", "empty", "null", "none", "undefined", "blank"}
NOTNULL_SYNONYMS = {"exists", "filled", "has value", "not null", "not missing", "not empty"}

# ──────────────────────────────────────────────
#  Ollama / LLM settings
# ──────────────────────────────────────────────
OLLAMA_MODEL = "phi35:latest"
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_TIMEOUT = 120


GROUP_BY_STOP_WORDS = {"here", "there", "clause", "where", "having", "order", "limit", "offset"}