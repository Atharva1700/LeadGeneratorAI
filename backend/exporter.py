import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import datetime, os
from db import get_enriched_leads, get_run, get_stats

def generate_excel(run_id: str) -> str:
    os.makedirs("output", exist_ok=True)
    date_str = datetime.date.today().strftime("%Y%m%d")
    filepath = f"output/capital_sense_leads_{date_str}.xlsx"
    
    wb = openpyxl.Workbook()
    # Need to implement _build_leads_sheet, _build_summary_sheet, _build_methodology_sheet
    wb.save(filepath)
    return filepath
