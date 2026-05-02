from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dispatcher.unified_db import UnifiedDB
from loguru import logger

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db = UnifiedDB()

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