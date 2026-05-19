import asyncio
import logging
from datetime import datetime, timezone
from db import update_run_status, log_progress
import phases.discovery as discovery
import phases.extraction as extraction
import phases.enrichment as enrichment
import phases.scoring as scoring
import phases.dedup as dedup
import exporter as exporter_module

logger = logging.getLogger(__name__)

async def run_pipeline(run_id: str, config: dict) -> None:
    now = lambda: datetime.now(timezone.utc).isoformat()
    try:
        update_run_status(run_id, "running", started_at=now())
        log_progress(run_id, "discovery", "Pipeline started — beginning source discovery", 0)

        # Phase 1
        raw_records = await discovery.run(run_id, config)
        log_progress(run_id, "discovery", f"Discovery complete — {len(raw_records)} raw records found", len(raw_records))
        update_run_status(run_id, "running", total_discovered=len(raw_records))

        # Phase 2
        await extraction.run(run_id, raw_records)
        log_progress(run_id, "extraction", "Extraction complete — fields parsed from all records", len(raw_records))

        # Phase 3
        await enrichment.run(run_id)
        from db import get_enriched_leads
        enriched_count = len(get_enriched_leads(run_id))
        log_progress(run_id, "enrichment", f"Enrichment complete — {enriched_count} leads enriched", enriched_count)
        update_run_status(run_id, "running", total_enriched=enriched_count)

        # Phase 4
        await scoring.run(run_id)
        log_progress(run_id, "scoring", "Scoring complete — all leads scored", enriched_count)
        update_run_status(run_id, "running", total_scored=enriched_count)

        # Phase 5
        final_count = await dedup.run(run_id, config)
        log_progress(run_id, "dedup", f"Dedup complete — {final_count} final qualifying leads", final_count)
        update_run_status(run_id, "running", total_final=final_count)

        # Export
        log_progress(run_id, "export", "Generating Excel file...", final_count)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, exporter_module.generate_excel, run_id)

        update_run_status(run_id, "completed", completed_at=now(), total_final=final_count)
        log_progress(run_id, "export", f"Done! {final_count} leads ready for download.", final_count)

    except Exception as e:
        logger.error(f"Pipeline {run_id} failed: {e}", exc_info=True)
        update_run_status(run_id, "failed", error_message=str(e))
        log_progress(run_id, "error", f"Pipeline failed: {str(e)}", 0)
