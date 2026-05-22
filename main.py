from fastapi import FastAPI,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dispatcher.unified_db import UnifiedDB
from loguru import logger
from processor.nlp_processor import NLPProcessor
from model.NLP_model import NLPRequest


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




@app.get("/api/stats")
def stats():
    return db.get_stats()

@app.get("/api/schema/{table_name}")
def get_table_schema(table_name: str):
    schema = db.get_table_schema(table_name)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")
    return {"table": table_name, "columns": schema}