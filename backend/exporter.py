"""
exporter.py — Generate formatted Excel output with 3 sheets.
"""

import datetime
import logging
import os

logger = logging.getLogger(__name__)


def generate_excel(run_id: str) -> str:
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter
    from db import get_enriched_leads, get_run, get_stats

    os.makedirs("output", exist_ok=True)
    date_str = datetime.date.today().strftime("%Y%m%d")
    filepath = f"output/capital_sense_leads_{date_str}.xlsx"

    wb = openpyxl.Workbook()
    _build_leads_sheet(wb, run_id, get_enriched_leads, get_stats)
    _build_summary_sheet(wb, run_id, get_run, get_stats)
    _build_methodology_sheet(wb)

    wb.save(filepath)
    logger.info(f"Excel saved: {filepath}")
    return filepath


def _build_leads_sheet(wb, run_id, get_enriched_leads, get_stats):
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter

    ws = wb.active
    ws.title = "Leads"

    headers = [
        "First Name", "Last Name", "Email", "Company Name", "Phone",
        "Industry", "Source", "Quality Score", "Business Age (mo.)", "Outreach Note"
    ]
    col_widths = [15, 15, 30, 28, 16, 18, 12, 14, 16, 50]

    # Header row
    header_fill = PatternFill(start_color="2C5F8A", end_color="2C5F8A", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.row_dimensions[1].height = 20

    # Get leads (all qualifying)
    from db import get_connection
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM enriched_leads
           WHERE run_id = ?
           ORDER BY quality_score DESC""",
        (run_id,)
    ).fetchall()
    conn.close()
    leads = [dict(r) for r in rows]

    # Write data rows
    score_fills = {
        90: PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"),
        80: PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid"),
        70: PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"),
        60: PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid"),
    }

    for row_num, lead in enumerate(leads, 2):
        score = lead.get("quality_score", 0) or 0
        row_fill = None
        for threshold in [90, 80, 70, 60]:
            if score >= threshold:
                row_fill = score_fills[threshold]
                break

        values = [
            lead.get("first_name", ""),
            lead.get("last_name", ""),
            lead.get("email", ""),
            lead.get("company_name", ""),
            lead.get("phone", ""),
            lead.get("industry", ""),
            lead.get("source", ""),
            score,
            lead.get("business_age_months", ""),
            lead.get("outreach_note", ""),
        ]

        for col, val in enumerate(values, 1):
            cell = ws.cell(row=row_num, column=col, value=val)
            cell.alignment = Alignment(vertical="center", wrap_text=(col == 10))
            if row_fill:
                cell.fill = row_fill

        ws.row_dimensions[row_num].height = 15

    # Column widths
    for col, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width

    # Freeze header + auto-filter
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:J1"


def _build_summary_sheet(wb, run_id, get_run, get_stats):
    from openpyxl.styles import PatternFill, Font
    ws = wb.create_sheet("Pipeline Summary")

    bold = Font(bold=True)
    header_fill = PatternFill(start_color="2C5F8A", end_color="2C5F8A", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    run = get_run(run_id) or {}
    stats = get_stats(run_id)

    row = 1

    def write_header(text, col=1):
        nonlocal row
        cell = ws.cell(row=row, column=col, value=text)
        cell.fill = header_fill
        cell.font = header_font
        row += 1

    def write_row(*values):
        nonlocal row
        for col, val in enumerate(values, 1):
            ws.cell(row=row, column=col, value=val)
        row += 1

    # Run Info
    write_header("Run Information")
    write_row("Run ID", run.get("run_id", ""))
    write_row("Status", run.get("status", ""))
    write_row("Started", run.get("started_at", ""))
    write_row("Completed", run.get("completed_at", ""))
    write_row("Total Discovered", run.get("total_discovered", 0))
    write_row("Total Enriched", run.get("total_enriched", 0))
    write_row("Total Scored", run.get("total_scored", 0))
    write_row("Total Final", run.get("total_final", 0))
    row += 1

    # Source Breakdown
    write_header("Source")
    ws.cell(row=row - 1, column=2, value="Count").font = header_font
    ws.cell(row=row - 1, column=2).fill = header_fill
    ws.cell(row=row - 1, column=3, value="%").font = header_font
    ws.cell(row=row - 1, column=3).fill = header_fill

    total_leads = sum(s.get("cnt", 0) for s in stats.get("sources", []))
    for src in stats.get("sources", []):
        pct = round(src["cnt"] / total_leads * 100, 1) if total_leads else 0
        write_row(src.get("source", ""), src.get("cnt", 0), f"{pct}%")
    row += 1

    # Score Distribution
    write_header("Score Range")
    ws.cell(row=row - 1, column=2, value="Count").font = header_font
    ws.cell(row=row - 1, column=2).fill = header_fill
    sd = stats.get("score_distribution", {})
    write_row("90-100", sd.get("s90", 0))
    write_row("80-89", sd.get("s80", 0))
    write_row("70-79", sd.get("s70", 0))
    write_row("60-69", sd.get("s60", 0))
    write_row("< 60", sd.get("slow", 0))
    row += 1

    # Validation Stats
    write_header("Validation Metric")
    ws.cell(row=row - 1, column=2, value="Count").font = header_font
    ws.cell(row=row - 1, column=2).fill = header_fill
    v = stats.get("validation", {})
    write_row("Emails Verified", v.get("emails_verified", 0))
    write_row("Phones Valid", v.get("phones_valid", 0))
    write_row("Both Valid", v.get("both_valid", 0))
    write_row("Total Leads", v.get("total", 0))

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 10


def _build_methodology_sheet(wb):
    ws = wb.create_sheet("Methodology")
    ws.merge_cells("A1:J30")
    cell = ws["A1"]
    cell.value = """Capital Sense Lead Generation — Methodology Notes

Data Sources:
- Google Maps (Playwright scraping): Business listings filtered by industry vertical and Chicago metro area
- Yelp Website (httpx + BeautifulSoup scraping): Public business listings, no API key
- Yellow Pages (httpx scraping): Additional business directory for Chicago
- SBA Public PPP Data: Businesses in Illinois that previously sought federal financing (highest intent signal)
- Illinois Secretary of State: Newly registered LLCs in good standing (business age proxy)

AI Processing:
- Llama 3.1 8B via Ollama (local, 100% free) used for HTML field extraction, lead quality scoring, and outreach note generation
- spaCy en_core_web_lg used for Named Entity Recognition on unstructured text
- Hunter.io free tier (25 searches/month) used for email enrichment when API key is provided

Scoring Methodology:
- Base score: field completeness (30pts) + email verified (15pts) + phone valid (15pts) + business age (20pts) + industry demand (20pts)
- AI score: Llama 3.1 holistic evaluation of financing likelihood (only for base_score >= 40)
- Final score: average of base and AI scores
- Minimum threshold: 60/100 to appear in final output

Data Quality:
- Email validation: pyIsEmail library + rejection of generic role addresses (info@, contact@, etc.)
- Phone validation: phonenumbers library, normalized to E.164
- Deduplication: recordlinkage fuzzy matching on company name (Jaro-Winkler 0.85 threshold) + phone
- Only leads with all 5 required fields (first name, last name, email, company, phone) included

Legal Compliance:
- Only publicly available business contact information collected
- All sources are public directories, government filings, or open scraping of public websites
- No personal/residential data collected
- Total cost of running this pipeline: $0.00
"""
    cell.alignment = __import__("openpyxl").styles.Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 100
    ws.row_dimensions[1].height = 400