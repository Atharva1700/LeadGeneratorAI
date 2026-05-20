"""
main.py — FastAPI backend. All routes defined here.
Run with: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging
import os
import uuid
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s"
)
logger = logging.getLogger(__name__)

import httpx
from fastapi import FastAPI, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

app = FastAPI(title="Capital Sense Lead Gen", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    from db import init_db
    init_db()
    logger.info("Database initialized")


# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            ollama_ok = r.status_code == 200
    except Exception:
        pass
    return {"status": "ok", "ollama": "connected" if ollama_ok else "unreachable"}


# ─── Pipeline ─────────────────────────────────────────────────────────────────

@app.post("/api/pipeline/start")
async def start_pipeline(background_tasks: BackgroundTasks, body: dict = {}):
    from db import create_run
    import pipeline as pipeline_module

    run_id = str(uuid.uuid4())
    config = {
        "target_count": int(body.get("target_count", os.getenv("TARGET_LEAD_COUNT", 200))),
        "min_score": int(body.get("min_score", os.getenv("MIN_QUALITY_SCORE", 60))),
        "verticals": body.get("verticals", [
            "restaurant", "trucking", "construction",
            "medical", "retail", "salon", "auto_repair", "landscaping"
        ]),
    }

    create_run(run_id)
    background_tasks.add_task(pipeline_module.run_pipeline, run_id, config)
    logger.info(f"Pipeline started: run_id={run_id}")
    return {"run_id": run_id, "status": "started"}


@app.get("/api/pipeline/status")
async def pipeline_status(run_id: str = Query(...)):
    from db import get_run, get_progress_events
    run = get_run(run_id)
    if not run:
        return JSONResponse(status_code=404, content={"error": "Run not found"})
    events = get_progress_events(run_id)[-5:]
    return {"run": run, "recent_events": events}


@app.get("/api/pipeline/stream")
async def stream_progress(run_id: str = Query(...)):
    from db import get_progress_events, get_run

    async def event_generator():
        last_id = 0
        consecutive_empty = 0
        max_empty = 120  # Stop polling after 120 empty checks (2 min with 1s sleep)

        while True:
            try:
                events = get_progress_events(run_id, after_id=last_id)
                if events:
                    consecutive_empty = 0
                    for event in events:
                        last_id = event["id"]
                        yield {
                            "data": json.dumps({
                                "phase": event["phase"],
                                "message": event["message"],
                                "leads_count": event["leads_count"],
                                "timestamp": event["timestamp"],
                            })
                        }
                else:
                    consecutive_empty += 1

                run = get_run(run_id)
                if run and run["status"] in ("completed", "failed"):
                    # Emit one more batch then stop
                    final_events = get_progress_events(run_id, after_id=last_id)
                    for event in final_events:
                        yield {
                            "data": json.dumps({
                                "phase": event["phase"],
                                "message": event["message"],
                                "leads_count": event["leads_count"],
                                "timestamp": event["timestamp"],
                            })
                        }
                    break

                if consecutive_empty > max_empty:
                    break

            except Exception as e:
                logger.warning(f"SSE stream error: {e}")
                yield {"data": json.dumps({"phase": "error", "message": str(e), "leads_count": 0, "timestamp": ""})}
                break

            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


# ─── Leads ────────────────────────────────────────────────────────────────────

@app.get("/api/leads")
async def get_leads(
    run_id: str = Query(...),
    page: int = Query(1),
    per_page: int = Query(50),
    min_score: int = Query(0),
):
    from db import get_connection
    offset = (page - 1) * per_page
    conn = get_connection()
    total = conn.execute(
        "SELECT COUNT(*) FROM enriched_leads WHERE run_id = ? AND quality_score >= ?",
        (run_id, min_score)
    ).fetchone()[0]

    rows = conn.execute(
        """SELECT * FROM enriched_leads
           WHERE run_id = ? AND quality_score >= ?
           ORDER BY quality_score DESC
           LIMIT ? OFFSET ?""",
        (run_id, min_score, per_page, offset)
    ).fetchall()
    conn.close()

    return {
        "leads": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@app.get("/api/leads/download")
async def download_leads(run_id: str = Query(...)):
    import exporter
    import datetime

    filepath = exporter.generate_excel(run_id)
    if not os.path.exists(filepath):
        return JSONResponse(status_code=404, content={"error": "Excel file not found"})

    filename = os.path.basename(filepath)
    return FileResponse(
        path=filepath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─── Stats ────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats_route(run_id: str = Query(...)):
    from db import get_stats
    return get_stats(run_id)


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)