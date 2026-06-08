from fastapi import FastAPI,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dispatcher.unified_db import UnifiedDB
from loguru import logger
from processor.nlp_processor import NLPProcessor
from model.NLP_model import NLPRequest
from typing import Optional


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db = UnifiedDB()
nlp_proc = NLPProcessor(db.catalog)
class ExecuteRequest(BaseModel):
    command: str

@app.post("/api/execute")
def execute(req: ExecuteRequest):
    logger.info(f"Received: {req.command}")
    result = db.execute(req.command)
    return {"success": True, "result": result}

@app.get("/")
def root():
    return {"message": "Unified DB API running"}


@app.post("/api/nlp")
def nlp_query(req: NLPRequest):
    command = nlp_proc.process(req.text)
    if not command:
        return {"success": False, "message": "Could not understand request"}
    # resp = db.execute(command)
    return {"success": True, "command": command}

@app.post("/api/nlp/llm")
def nlp_llm_only(req: NLPRequest):
    command = nlp_proc._llm_fallback(req.text)
    return {"command": command}




@app.get("/api/databases")
def list_databases():
    return {"databases": db.get_all_databases()}

@app.get("/api/stats")
def stats(db_name: Optional[str] = None):
    if db_name:
        # Stats for a specific database
        stats_data = db.get_stats_for_database(db_name)
        stats_data["database"] = db_name
        return stats_data
    else:
        # Stats for all databases
        all_stats = {}
        for name in db.get_all_databases():
            all_stats[name] = db.get_stats_for_database(name)
        return all_stats

@app.get("/api/schema/{table_name}")
def get_table_schema(table_name: str):
    schema = db.get_table_schema(table_name)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")
    return {"table": table_name, "columns": schema}

# ---------- Database metadata endpoints ----------
@app.get("/api/current_database")
def current_database():
    try:
        current = db.get_current_database()
        return {"database": current}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Add httpx to your imports at top of main.py
# (already used inside nlp_processor now, but good to be aware of)

# ── Request model ─────────────────────────────────────────────────────────
class ExplainRequest(BaseModel):
    command: str

# ── Endpoint ──────────────────────────────────────────────────────────────
@app.post("/api/explain")
def explain_query(req: ExplainRequest):
    """
    Generate a plain-English explanation of a SQL/NoSQL command.
    
    Request body:
        { "command": "SQL SELECT AVG(age) FROM users WHERE city = 'Boston'" }
    
    Response (success):
        { "success": true, "command": "SQL SELECT ...", "explanation": "..." }
    
    Response (failure):
        { "success": false, "message": "Could not generate explanation" }
    """
    command = req.command.strip()
    
    if not command:
        raise HTTPException(status_code=400, detail="Command cannot be empty")
    
    logger.info(f"[Explain] Request for command: {command[:80]}")
    
    explanation = nlp_proc.explain_command(command)
    
    if not explanation:
        logger.warning(f"[Explain] Failed to explain: {command[:80]}")
        return {
            "success": False,
            "message": "Could not generate explanation. Model may be unavailable."
        }
    
    logger.info(f"[Explain] Success: {explanation[:80]}")
    return {
        "success": True,
        "command": command,
        "explanation": explanation
    }
    
# ── Request model ─────────────────────────────────────────────────────────
class FixRequest(BaseModel):
    failed_query: str
    error_message: str
    auto_retry: bool = False   # False by default — safer, user confirms first

# ── Endpoint ──────────────────────────────────────────────────────────────
@app.post("/api/execute/fix")
def fix_query(req: FixRequest):
    """
    Attempt to fix a failed query using AI error correction.

    Request body:
        {
          "failed_query": "SELECT * FROM user WHERE id = 1",
          "error_message": "Table 'user' does not exist",
          "auto_retry": false
        }

    Response (fix found, auto_retry=false):
        { "success": true, "fixed_query": "SQL SELECT * FROM users WHERE id = 1" }

    Response (fix found, auto_retry=true):
        { "success": true, "fixed_query": "...", "result": {...} }

    Response (fix failed):
        { "success": false, "message": "Could not fix the query" }
    """
    failed_query  = req.failed_query.strip()
    error_message = req.error_message.strip()

    if not failed_query or not error_message:
        raise HTTPException(status_code=400, detail="failed_query and error_message are required")

    logger.info(f"[Fix] Attempting fix for: {failed_query[:80]}")

    fixed = nlp_proc.fix_failed_query(failed_query, error_message)

    if not fixed:
        logger.warning(f"[Fix] Could not fix: {failed_query[:80]}")
        return {"success": False, "message": "Could not fix the query"}

    logger.info(f"[Fix] Fixed query: {fixed[:80]}")

    # auto_retry=True: execute the fix immediately and return results
    if req.auto_retry:
        try:
            resp = db.execute(fixed)
            return {
                "success": True,
                "fixed_query": fixed,
                "result": resp.to_dict()
            }
        except Exception as e:
            # Fix looked valid but still failed — return fix without result
            logger.error(f"[Fix] Auto-retry failed: {e}")
            return {
                "success": True,
                "fixed_query": fixed,
                "result": None,
                "retry_error": str(e)
            }

    # auto_retry=False: return the fix, let frontend confirm before executing
    return {"success": True, "fixed_query": fixed}


class SuggestQueryRequest(BaseModel):
    partial_query: str
    limit: int = 1

@app.post("/api/query/suggest")
async def suggest_query(req: SuggestQueryRequest):
    suggestions = nlp_proc.suggest_full_queries(req.partial_query, req.limit)
    return {"suggestions": suggestions}