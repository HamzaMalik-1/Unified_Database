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
    resp = db.execute(command)
    return {"success": True, "command": command, "result": resp.to_dict()}

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
