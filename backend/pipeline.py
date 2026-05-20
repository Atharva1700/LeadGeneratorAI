"""
pipeline.py — Orchestrator. Runs all 5 phases in sequence with SSE progress events.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).isoformat()


async def run_pipeline(run_id: str, config: dict) -> None:
    from db import update_run_status, log_progress, get_enriched_leads
    import phases.discovery as discovery
    import phases.extraction as extraction
    import phases.enrichment as enrichment
    import phases.scoring as scoring
    import phases.dedup as dedup
    import exporter as exporter_module

    try:
        update_run_status(run_id, "running", started_at=_now())
        log_progress(run_id, "discovery", "Pipeline started — beginning source discovery", 0)

        # ── Phase 1: Discovery ──────────────────────────────────────────────
        raw_records = await discovery.run(run_id, config)
        update_run_status(run_id, "running", total_discovered=len(raw_records))
        log_progress(run_id, "discovery",
                     f"Discovery complete — {len(raw_records)} raw records found",
                     len(raw_records))

        if len(raw_records) == 0:
            log_progress(run_id, "error",
                         "Discovery found 0 records. Check that Playwright/Chromium is installed "
                         "and internet is accessible. Pipeline aborted.", 0)
            update_run_status(run_id, "failed",
                              error_message="Discovery returned 0 records")
            return

        # ── Phase 2: Extraction ─────────────────────────────────────────────
        log_progress(run_id, "extraction",
                     f"Extracting fields from {len(raw_records)} records...", 0)
        await extraction.run(run_id, raw_records)
        log_progress(run_id, "extraction",
                     "Extraction complete — fields parsed from all records",
                     len(raw_records))

        # ── Phase 3: Enrichment ─────────────────────────────────────────────
        log_progress(run_id, "enrichment", "Enriching leads with contact data...", 0)
        await enrichment.run(run_id)
        enriched = get_enriched_leads(run_id)
        enriched_count = len(enriched)
        update_run_status(run_id, "running", total_enriched=enriched_count)
        log_progress(run_id, "enrichment",
                     f"Enrichment complete — {enriched_count} leads enriched",
                     enriched_count)

        # ── Phase 4: Scoring ────────────────────────────────────────────────
        log_progress(run_id, "scoring", "Scoring all leads for financing likelihood...", 0)
        await scoring.run(run_id)
        update_run_status(run_id, "running", total_scored=enriched_count)
        log_progress(run_id, "scoring",
                     "Scoring complete — all leads scored", enriched_count)

        # ── Phase 5: Dedup + Filter ─────────────────────────────────────────
        log_progress(run_id, "dedup", "Deduplicating and filtering final leads...", 0)
        final_count = await dedup.run(run_id, config)
        update_run_status(run_id, "running", total_final=final_count)
        log_progress(run_id, "dedup",
                     f"Dedup complete — {final_count} final qualifying leads",
                     final_count)

        # ── Export ──────────────────────────────────────────────────────────
        log_progress(run_id, "export", "Generating Excel file...", final_count)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, exporter_module.generate_excel, run_id)

        update_run_status(run_id, "completed",
                          completed_at=_now(),
                          total_final=final_count)
        log_progress(run_id, "export",
                     f"Done! {final_count} leads ready for download.", final_count)

    except Exception as e:
        logger.error(f"Pipeline {run_id} failed: {e}", exc_info=True)
        try:
            update_run_status(run_id, "failed", error_message=str(e)[:500])
            log_progress(run_id, "error", f"Pipeline failed: {str(e)[:300]}", 0)
        except Exception:
            pass