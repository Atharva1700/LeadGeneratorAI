import json
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
import asyncio, uuid, os
from db import init_db
import pipeline as pipeline_module
import httpx
from dotenv import load_dotenv

load_dotenv()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

app = FastAPI(title="Capital Sense Lead Gen", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    init_db()

@app.post("/api/pipeline/start")
async def start_pipeline(background_tasks: BackgroundTasks, body: dict = {}):
    run_id = str(uuid.uuid4())
    config = {
        "target_count": body.get("target_count", int(os.getenv("TARGET_LEAD_COUNT", 200))),
        "min_score": body.get("min_score", int(os.getenv("MIN_QUALITY_SCORE", 60))),
        "verticals": body.get("verticals", [
            "restaurant", "trucking", "construction",
            "medical", "retail", "salon", "auto_repair", "landscaping"
        ])
    }
    from db import create_run
    create_run(run_id)
    background_tasks.add_task(pipeline_module.run_pipeline, run_id, config)
    return {"run_id": run_id, "status": "started"}

@app.get("/api/pipeline/stream")
async def stream_progress(run_id: str):
    async def event_generator():
        last_id = 0
        while True:
            from db import get_progress_events, get_run
            events = get_progress_events(run_id, after_id=last_id)
            for event in events:
                last_id = event["id"]
                yield {"data": json.dumps({
                    "phase": event["phase"],
                    "message": event["message"],
                    "leads_count": event["leads_count"],
                    "timestamp": event["timestamp"]
                })}
            run = get_run(run_id)
            if run and run.get("status") in ("completed", "failed"):
                break
            await asyncio.sleep(1)
    return EventSourceResponse(event_generator())

@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
            ollama_ok = r.status_code == 200
    except Exception:
        ollama_ok = False
    return {"status": "ok", "ollama": "connected" if ollama_ok else "unreachable"}
