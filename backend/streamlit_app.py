import html
import json
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from backend.utils.po_processor import (
    database_status,
    database_summary,
    process_uploaded_pdfs,
)
from backend.utils.database import get_latest_records
from backend.utils.output_writer import write_clean_json_outputs, write_response_export_bundle


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "outputs"
CHROMA_DIR = BASE_DIR / "chroma_db"
CHROMA_TMP_DIR = BASE_DIR / "chroma_tmp"
INCOMING_DIR = BASE_DIR / "incoming_pdfs"
PROCESSED_DIR = BASE_DIR / "processed_pdfs"
FAILED_DIR = BASE_DIR / "failed_pdfs"
WATCHER_PATH = BASE_DIR / "backend" / "watcher.py"
HISTORY_PATH = OUTPUT_DIR / "processed_po_history.json"

OUTPUT_DIR.mkdir(exist_ok=True)
CHROMA_DIR.mkdir(exist_ok=True)
CHROMA_TMP_DIR.mkdir(exist_ok=True)
INCOMING_DIR.mkdir(exist_ok=True)
PROCESSED_DIR.mkdir(exist_ok=True)
FAILED_DIR.mkdir(exist_ok=True)


# ─── helpers ───────────────────────────────────────────────────────────────────

def reset_persistent_vector_database() -> tuple[bool, str]:
    try:
        st.cache_resource.clear()
        resolved_base = BASE_DIR.resolve()
        for target in [CHROMA_DIR, CHROMA_TMP_DIR]:
            resolved_target = target.resolve()
            if resolved_base not in resolved_target.parents and resolved_target != resolved_base:
                return False, f"Refusing to delete unexpected path: {resolved_target}"
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(exist_ok=True)
        return True, "Vector database cache cleared."
    except Exception as exc:
        return False, f"Could not clear vector database cache: {type(exc).__name__}: {exc}"


def status_badge(status: str) -> str:
    css_class = {
        "Completed": "badge-completed",
        "Needs review": "badge-review",
        "Failed": "badge-failed",
    }.get(status, "badge-review")
    return f'<span class="status-badge {css_class}">{html.escape(status or "Unknown")}</span>'


def status_cell_style(value: str) -> str:
    if value in {"Completed", "Extracted"}:
        return "background-color: rgba(16, 185, 129, 0.15); color: #34d399; font-weight: 600;"
    if value in {"Needs review", "Needs Review"}:
        return "background-color: rgba(245, 158, 11, 0.15); color: #fbbf24; font-weight: 600;"
    if value == "Failed":
        return "background-color: rgba(239, 68, 68, 0.15); color: #f87171; font-weight: 600;"
    return "color: #94a3b8;"


def dataframe_with_status(df: pd.DataFrame):
    status_column = None
    for column in ["extraction_status", "Extraction Status", "Status"]:
        if column in df.columns:
            status_column = column
            break
    if status_column is None or df.empty:
        return df
    return df.style.map(status_cell_style, subset=[status_column])


FRIENDLY_COLUMN_NAMES = {
    "file_name": "File Name",
    "po_number": "PO Number",
    "po_date": "PO Date",
    "buyer_name": "Buyer Name",
    "billing_address": "Billing Address",
    "billing_state": "Billing State",
    "billing_pincode": "Billing Pincode",
    "billing_gst_number": "Billing GST Number",
    "total_amount": "Total Amount",
    "extraction_status": "Status",
    "item_description": "Item Description",
    "hsn_sac": "HSN/SAC",
    "quantity": "Quantity",
    "uom": "UOM",
    "unit_price": "Unit Price",
    "tax_percent": "Tax %",
    "line_total": "Line Total",
}


def friendly_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={k: v for k, v in FRIENDLY_COLUMN_NAMES.items() if k in df.columns})


def display_history_status(value: str | None) -> str:
    if value == "Completed":
        return "Extracted"
    if value == "Failed":
        return "Failed"
    if value == "Needs review":
        return "Needs Review"
    return "Needs Review" if value else "Needs Review"


def display_history_time(value) -> str:
    if value is None or value == "" or pd.isna(value):
        return "Not available"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def history_source_from_moved_to(value: str | None) -> str:
    if value in {"processed_pdfs", "failed_pdfs"}:
        return "Auto Folder Upload"
    return "Manual Upload"


def load_output_history_rows() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(history, list):
        return []
    rows = []
    for document in history:
        if not isinstance(document, dict):
            continue
        data = document.get("data") or {}
        items = document.get("items") or []
        rows.append(
            {
                "File Name": document.get("file_name") or "Not available",
                "Upload/Processed Time": display_history_time(document.get("processed_at")),
                "Status": display_history_status(document.get("extraction_status")),
                "PO Number": data.get("po_number") or "Not available",
                "Buyer Name": data.get("buyer_name") or "Not available",
                "Total Items": len(items),
                "Source": history_source_from_moved_to(document.get("moved_to")),
            }
        )
    return rows


def current_document_history_rows(documents: list[dict], payload_source: str | None) -> list[dict]:
    source = "Auto Folder Upload" if payload_source == "incoming_pdfs" else "Manual Upload"
    rows = []
    for document in documents:
        data = document.get("data") or {}
        debug = document.get("debug") or {}
        items = document.get("items") or []
        rows.append(
            {
                "File Name": document.get("file_name") or "Not available",
                "Upload/Processed Time": "Not available",
                "Status": display_history_status(debug.get("extraction_status")),
                "PO Number": data.get("po_number") or "Not available",
                "Buyer Name": data.get("buyer_name") or "Not available",
                "Total Items": len(items),
                "Source": source,
            }
        )
    return rows


def database_history_rows(limit: int = 500) -> list[dict]:
    records = get_latest_records(limit=limit)
    headers_df = records.get("headers", pd.DataFrame())
    items_df = records.get("items", pd.DataFrame())
    if headers_df.empty:
        return []
    item_counts = {}
    if not items_df.empty and {"file_name", "po_number"}.issubset(items_df.columns):
        item_counts = items_df.groupby(["file_name", "po_number"]).size().to_dict()
    output_rows = load_output_history_rows()
    source_by_file = {
        row.get("File Name"): row.get("Source", "Manual Upload")
        for row in output_rows
        if row.get("File Name")
    }
    rows = []
    for _, row in headers_df.iterrows():
        file_name = row.get("file_name") or "Not available"
        po_number = row.get("po_number") or "Not available"
        rows.append(
            {
                "File Name": file_name,
                "Upload/Processed Time": display_history_time(row.get("created_at")),
                "Status": display_history_status(row.get("extraction_status")),
                "PO Number": po_number,
                "Buyer Name": row.get("buyer_name") or "Not available",
                "Total Items": item_counts.get((file_name, po_number), 0),
                "Source": source_by_file.get(file_name, "Manual Upload"),
            }
        )
    return rows


def build_upload_history_df(documents: list[dict], payload_source: str | None) -> pd.DataFrame:
    rows = database_history_rows()
    rows.extend(load_output_history_rows())
    rows.extend(current_document_history_rows(documents, payload_source))
    deduped = []
    seen = set()
    for row in rows:
        key = (
            row.get("File Name"),
            row.get("PO Number"),
            row.get("Upload/Processed Time"),
            row.get("Source"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return pd.DataFrame(deduped)


def remove_empty_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.dropna(axis=1, how="all")


def format_numeric_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    formatted = df.copy()
    for column in columns:
        if column in formatted.columns:
            formatted[column] = pd.to_numeric(formatted[column], errors="coerce")
    return formatted


def check_api_status() -> dict:
    return {
        "api_connected": True,
        "status": "ok",
        "database": database_status(),
    }


def process_files_for_streamlit(uploaded_files) -> tuple[bool, dict]:
    try:
        return True, process_uploaded_pdfs(uploaded_files, include_debug=True)
    except Exception as exc:
        return False, {
            "status_code": 500,
            "success": False,
            "message": f"Processing failed: {type(exc).__name__}: {exc}",
            "documents": [],
            "debug": {"warnings": [f"Processing failed: {type(exc).__name__}: {exc}"]},
        }


# ─── CSS ───────────────────────────────────────────────────────────────────────

def load_custom_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

        /* ── design tokens ─────────────────────────────────── */
        :root {
            --bg-main:        #070A12;
            --bg-panel:       #0F172A;
            --bg-card:        rgba(15, 23, 42, 0.78);
            --border:         rgba(148, 163, 184, 0.18);
            --border-strong:  rgba(148, 163, 184, 0.32);
            --text-main:      #F8FAFC;
            --text-muted:     #94A3B8;
            --text-subtle:    #64748B;
            --accent-blue:    #4F7CFF;
            --accent-cyan:    #18C8FF;
            --accent-purple:  #7C3AED;
            --success:        #34d399;
            --warning:        #fbbf24;
            --danger:         #f87171;
            --gradient-btn:   linear-gradient(135deg, #4F7CFF 0%, #18C8FF 100%);
            --gradient-glow:  linear-gradient(135deg, rgba(79,124,255,0.15) 0%, rgba(24,200,255,0.08) 100%);
            --shadow-card:    0 4px 24px rgba(0,0,0,0.45), 0 1px 4px rgba(0,0,0,0.3);
            --shadow-glow:    0 0 40px rgba(79,124,255,0.12);
        }

        /* ── global reset ───────────────────────────────────── */
        html, body, .stApp {
            font-family: 'Inter', system-ui, -apple-system, sans-serif !important;
            background-color: var(--bg-main) !important;
            color: var(--text-main) !important;
        }

        /* ── starfield background ───────────────────────────── */
        .stApp::before {
            content: "";
            position: fixed;
            inset: 0;
            background-image:
                radial-gradient(1px 1px at 12% 18%, rgba(255,255,255,0.35) 0%, transparent 100%),
                radial-gradient(1px 1px at 34% 62%, rgba(255,255,255,0.25) 0%, transparent 100%),
                radial-gradient(1px 1px at 56% 9%, rgba(255,255,255,0.3) 0%, transparent 100%),
                radial-gradient(1px 1px at 78% 44%, rgba(255,255,255,0.2) 0%, transparent 100%),
                radial-gradient(1px 1px at 91% 77%, rgba(255,255,255,0.28) 0%, transparent 100%),
                radial-gradient(1px 1px at 23% 88%, rgba(255,255,255,0.22) 0%, transparent 100%),
                radial-gradient(1px 1px at 67% 33%, rgba(255,255,255,0.18) 0%, transparent 100%),
                radial-gradient(1.5px 1.5px at 45% 55%, rgba(79,124,255,0.4) 0%, transparent 100%),
                radial-gradient(1.5px 1.5px at 82% 21%, rgba(24,200,255,0.35) 0%, transparent 100%),
                radial-gradient(600px 400px at 20% 30%, rgba(79,124,255,0.04) 0%, transparent 70%),
                radial-gradient(500px 350px at 80% 70%, rgba(124,58,237,0.04) 0%, transparent 70%);
            pointer-events: none;
            z-index: 0;
        }

        .block-container {
            position: relative;
            z-index: 1;
            padding-top: 2rem !important;
            padding-bottom: 4rem !important;
            max-width: 1200px !important;
        }

        /* ── sidebar ────────────────────────────────────────── */
        [data-testid="stSidebar"] {
            background: rgba(7, 10, 18, 0.96) !important;
            border-right: 1px solid var(--border) !important;
            backdrop-filter: blur(20px);
        }

        [data-testid="stSidebar"] > div {
            background: transparent !important;
        }

        /* sidebar brand */
        .sb-brand {
            padding: 1.2rem 0 1.4rem;
            border-bottom: 1px solid var(--border);
            margin-bottom: 1.2rem;
        }
        .sb-brand-name {
            font-size: 1.0rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            background: linear-gradient(135deg, #4F7CFF, #18C8FF);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            line-height: 1.2;
        }
        .sb-brand-sub {
            font-size: 0.72rem;
            font-weight: 500;
            color: var(--text-subtle);
            margin-top: 3px;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }

        /* sidebar nav label */
        .sb-nav-label {
            font-size: 0.65rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: var(--text-subtle);
            margin-bottom: 0.5rem;
        }

        /* sidebar nav buttons */
        [data-testid="stSidebar"] .stButton > button {
            background: transparent !important;
            border: 1px solid transparent !important;
            border-radius: 8px !important;
            color: var(--text-muted) !important;
            font-size: 0.88rem !important;
            font-weight: 500 !important;
            padding: 9px 14px !important;
            text-align: left !important;
            width: 100% !important;
            transition: all 0.18s ease !important;
            box-shadow: none !important;
            margin-bottom: 2px !important;
        }

        [data-testid="stSidebar"] .stButton > button:hover {
            background: rgba(79, 124, 255, 0.1) !important;
            border-color: rgba(79, 124, 255, 0.25) !important;
            color: var(--text-main) !important;
        }

        [data-testid="stSidebar"] .stButton > button[data-active="true"],
        [data-testid="stSidebar"] .stButton.active-nav > button {
            background: linear-gradient(135deg, rgba(79,124,255,0.2), rgba(24,200,255,0.12)) !important;
            border-color: rgba(79,124,255,0.4) !important;
            color: #a5c8ff !important;
            font-weight: 600 !important;
        }

        /* ── page heading ────────────────────────────────────── */
        .page-heading {
            margin-bottom: 1.6rem;
            padding-bottom: 1.2rem;
            border-bottom: 1px solid var(--border);
        }
        .page-eyebrow {
            font-size: 0.68rem;
            font-weight: 700;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            background: linear-gradient(90deg, #4F7CFF, #18C8FF);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 0.4rem;
        }
        .page-title {
            font-size: 1.65rem;
            font-weight: 800;
            letter-spacing: -0.025em;
            color: var(--text-main);
            margin: 0 0 0.35rem;
            line-height: 1.2;
        }
        .page-subtitle {
            font-size: 0.92rem;
            color: var(--text-muted);
            line-height: 1.6;
            margin: 0;
            max-width: 560px;
        }

        /* ── glass cards ─────────────────────────────────────── */
        .glass-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 16px;
            box-shadow: var(--shadow-card);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            padding: 22px 24px;
            margin-bottom: 1rem;
            position: relative;
            overflow: hidden;
        }
        .glass-card::before {
            content: "";
            position: absolute;
            inset: 0;
            background: var(--gradient-glow);
            pointer-events: none;
            border-radius: inherit;
        }
        .glass-card-title {
            font-size: 0.95rem;
            font-weight: 700;
            color: var(--text-main);
            margin: 0 0 0.3rem;
        }
        .glass-card-copy {
            font-size: 0.84rem;
            color: var(--text-muted);
            line-height: 1.55;
            margin: 0;
        }

        /* ── upload zone ─────────────────────────────────────── */
        .upload-card {
            background: var(--bg-card);
            border: 1px dashed rgba(79, 124, 255, 0.4);
            border-radius: 16px;
            backdrop-filter: blur(16px);
            padding: 0;
            overflow: hidden;
            box-shadow: var(--shadow-card), 0 0 30px rgba(79,124,255,0.07);
        }
        .upload-card-header {
            padding: 20px 24px 16px;
            border-bottom: 1px solid var(--border);
            background: rgba(79,124,255,0.04);
        }
        .upload-card-label {
            font-size: 0.68rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: var(--accent-cyan);
            margin-bottom: 0.3rem;
        }
        .upload-card-title {
            font-size: 1.0rem;
            font-weight: 700;
            color: var(--text-main);
            margin-bottom: 0.2rem;
        }
        .upload-card-hint {
            font-size: 0.82rem;
            color: var(--text-muted);
        }
        .upload-card-body {
            padding: 16px 20px 20px;
        }

        /* ── file uploader overrides ─────────────────────────── */
        [data-testid="stFileUploader"] {
            background: transparent !important;
        }
        [data-testid="stFileUploader"] section {
            background: rgba(15, 23, 42, 0.5) !important;
            border: 1px dashed var(--border) !important;
            border-radius: 10px !important;
        }
        [data-testid="stFileUploader"] label,
        [data-testid="stFileUploader"] span,
        [data-testid="stFileUploader"] p {
            color: var(--text-muted) !important;
        }

        /* ── metric cards ─────────────────────────────────────── */
        [data-testid="stMetric"] {
            background: var(--bg-card) !important;
            border: 1px solid var(--border) !important;
            border-radius: 12px !important;
            box-shadow: var(--shadow-card) !important;
            backdrop-filter: blur(12px) !important;
            padding: 16px 18px !important;
        }
        [data-testid="stMetricLabel"] {
            color: var(--text-muted) !important;
            font-size: 0.75rem !important;
            font-weight: 600 !important;
            letter-spacing: 0.06em !important;
            text-transform: uppercase !important;
        }
        [data-testid="stMetricValue"] {
            color: var(--text-main) !important;
            font-size: 1.5rem !important;
            font-weight: 700 !important;
        }
        [data-testid="stMetricDelta"] {
            color: var(--text-muted) !important;
            font-size: 0.78rem !important;
        }
        [data-testid="stMetricDelta"] svg { display: none; }

        /* ── buttons (main area) ─────────────────────────────── */
        .stButton > button {
            border-radius: 8px !important;
            font-size: 0.88rem !important;
            font-weight: 600 !important;
            min-height: 40px !important;
            transition: all 0.18s ease !important;
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #4F7CFF 0%, #18C8FF 100%) !important;
            border: none !important;
            color: #ffffff !important;
            box-shadow: 0 4px 20px rgba(79,124,255,0.4) !important;
        }
        .stButton > button[kind="primary"]:hover {
            box-shadow: 0 6px 28px rgba(79,124,255,0.55) !important;
            transform: translateY(-1px) !important;
            filter: brightness(1.06) !important;
        }
        .stButton > button[kind="primary"]:active {
            transform: translateY(0) !important;
        }
        .stButton > button[kind="secondary"] {
            background: rgba(148,163,184,0.08) !important;
            border: 1px solid var(--border) !important;
            color: var(--text-muted) !important;
        }
        .stButton > button[kind="secondary"]:hover {
            background: rgba(148,163,184,0.14) !important;
            border-color: var(--border-strong) !important;
            color: var(--text-main) !important;
        }

        /* ── download button ─────────────────────────────────── */
        .stDownloadButton > button {
            background: rgba(79,124,255,0.12) !important;
            border: 1px solid rgba(79,124,255,0.35) !important;
            border-radius: 8px !important;
            color: #a5c8ff !important;
            font-weight: 600 !important;
        }
        .stDownloadButton > button:hover {
            background: rgba(79,124,255,0.2) !important;
            border-color: rgba(79,124,255,0.6) !important;
            color: #d0e5ff !important;
        }

        /* ── tabs ────────────────────────────────────────────── */
        div[data-testid="stTabs"] [role="tablist"] {
            background: rgba(15, 23, 42, 0.7) !important;
            border: 1px solid var(--border) !important;
            border-radius: 10px !important;
            gap: 2px !important;
            padding: 5px !important;
            backdrop-filter: blur(12px) !important;
        }
        div[data-testid="stTabs"] button[role="tab"] {
            border-radius: 7px !important;
            color: var(--text-muted) !important;
            font-size: 0.86rem !important;
            font-weight: 500 !important;
            padding: 8px 14px !important;
            transition: all 0.15s !important;
            background: transparent !important;
        }
        div[data-testid="stTabs"] button[role="tab"]:hover {
            background: rgba(79,124,255,0.1) !important;
            color: var(--text-main) !important;
        }
        div[data-testid="stTabs"] button[aria-selected="true"] {
            background: linear-gradient(135deg, rgba(79,124,255,0.25), rgba(24,200,255,0.15)) !important;
            color: #a5c8ff !important;
            font-weight: 700 !important;
            border: 1px solid rgba(79,124,255,0.3) !important;
        }

        /* ── data tables ─────────────────────────────────────── */
        div[data-testid="stDataFrame"] {
            border: 1px solid var(--border) !important;
            border-radius: 12px !important;
            overflow: hidden !important;
            box-shadow: var(--shadow-card) !important;
        }
        div[data-testid="stDataFrame"] iframe {
            background: var(--bg-panel) !important;
        }

        /* ── expanders ────────────────────────────────────────── */
        [data-testid="stExpander"] {
            background: var(--bg-card) !important;
            border: 1px solid var(--border) !important;
            border-radius: 12px !important;
            backdrop-filter: blur(12px) !important;
            overflow: hidden !important;
        }
        [data-testid="stExpander"] summary {
            color: var(--text-main) !important;
            font-weight: 600 !important;
        }

        /* ── alerts ───────────────────────────────────────────── */
        .stAlert {
            border-radius: 10px !important;
            backdrop-filter: blur(8px) !important;
        }
        div[data-testid="stAlert"] {
            background: rgba(15,23,42,0.7) !important;
            border-color: var(--border) !important;
        }

        /* ── inputs / selects ─────────────────────────────────── */
        [data-testid="stTextInput"] input,
        [data-testid="stSelectbox"] > div > div {
            background: rgba(15, 23, 42, 0.8) !important;
            border: 1px solid var(--border) !important;
            border-radius: 8px !important;
            color: var(--text-main) !important;
        }
        [data-testid="stTextInput"] input:focus {
            border-color: rgba(79,124,255,0.5) !important;
            box-shadow: 0 0 0 3px rgba(79,124,255,0.12) !important;
        }

        /* ── progress bar ─────────────────────────────────────── */
        [data-testid="stProgress"] > div {
            background: rgba(148,163,184,0.12) !important;
            border-radius: 999px !important;
        }
        [data-testid="stProgress"] > div > div {
            background: linear-gradient(90deg, #4F7CFF, #18C8FF) !important;
        }

        /* ── spinner ──────────────────────────────────────────── */
        [data-testid="stSpinner"] {
            color: var(--accent-cyan) !important;
        }

        /* ── status badges ────────────────────────────────────── */
        .status-badge {
            border-radius: 999px;
            display: inline-block;
            font-size: 0.75rem;
            font-weight: 700;
            padding: 3px 10px;
        }
        .badge-completed {
            background: rgba(16,185,129,0.15);
            border: 1px solid rgba(52,211,153,0.3);
            color: #34d399;
        }
        .badge-review {
            background: rgba(245,158,11,0.15);
            border: 1px solid rgba(251,191,36,0.3);
            color: #fbbf24;
        }
        .badge-failed {
            background: rgba(239,68,68,0.15);
            border: 1px solid rgba(248,113,113,0.3);
            color: #f87171;
        }

        /* ── folder cards ─────────────────────────────────────── */
        .folder-glass-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 14px;
            box-shadow: var(--shadow-card);
            backdrop-filter: blur(14px);
            padding: 20px 18px;
            min-height: 120px;
            transition: box-shadow 0.18s, transform 0.18s;
        }
        .folder-glass-card:hover {
            box-shadow: var(--shadow-card), 0 0 30px rgba(79,124,255,0.1);
            transform: translateY(-2px);
        }
        .folder-count {
            font-size: 2rem;
            font-weight: 800;
            line-height: 1;
            margin-bottom: 0.3rem;
        }
        .folder-count-blue  { color: var(--accent-blue); }
        .folder-count-green { color: var(--success); }
        .folder-count-warn  { color: var(--warning); }
        .folder-count-cyan  { color: var(--accent-cyan); }
        .folder-card-title {
            color: var(--text-main);
            font-size: 0.88rem;
            font-weight: 700;
            margin-bottom: 0.2rem;
        }
        .folder-card-copy {
            color: var(--text-muted);
            font-size: 0.78rem;
            line-height: 1.4;
        }

        /* ── export cards ─────────────────────────────────────── */
        .export-glass-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 14px;
            box-shadow: var(--shadow-card);
            backdrop-filter: blur(14px);
            padding: 18px 20px;
            margin-bottom: 0.6rem;
            min-height: 100px;
            position: relative;
            overflow: hidden;
        }
        .export-glass-card::before {
            content: "";
            position: absolute;
            inset: 0;
            background: linear-gradient(135deg, rgba(79,124,255,0.04), transparent 60%);
            pointer-events: none;
        }
        .export-type-tag {
            display: inline-block;
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            padding: 3px 9px;
            border-radius: 6px;
            background: rgba(79,124,255,0.15);
            border: 1px solid rgba(79,124,255,0.25);
            color: #a5c8ff;
            margin-bottom: 0.6rem;
        }
        .export-card-title {
            font-size: 0.92rem;
            font-weight: 700;
            color: var(--text-main);
            margin-bottom: 0.25rem;
        }
        .export-card-copy {
            font-size: 0.82rem;
            color: var(--text-muted);
            line-height: 1.45;
        }

        /* ── empty state ──────────────────────────────────────── */
        .empty-state {
            text-align: center;
            padding: 3rem 2rem;
            background: var(--bg-card);
            border: 1px dashed var(--border);
            border-radius: 16px;
            backdrop-filter: blur(12px);
        }
        .empty-state-icon {
            font-size: 2.4rem;
            margin-bottom: 0.8rem;
            opacity: 0.5;
            filter: grayscale(0.6);
        }
        .empty-state-title {
            font-size: 1rem;
            font-weight: 700;
            color: var(--text-main);
            margin-bottom: 0.35rem;
        }
        .empty-state-copy {
            font-size: 0.88rem;
            color: var(--text-muted);
            line-height: 1.55;
            max-width: 340px;
            margin: 0 auto;
        }

        /* ── misc ────────────────────────────────────────────── */
        .divider {
            border: none;
            border-top: 1px solid var(--border);
            margin: 1rem 0;
        }
        .compact-label {
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--text-muted);
            letter-spacing: 0.04em;
            margin: 0.8rem 0 0.4rem;
        }
        code {
            background: rgba(79,124,255,0.12) !important;
            border-radius: 5px !important;
            color: #a5c8ff !important;
            padding: 2px 6px !important;
            font-size: 0.85em !important;
        }

        /* ── scrollbar ────────────────────────────────────────── */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: var(--bg-panel); }
        ::-webkit-scrollbar-thumb {
            background: rgba(148,163,184,0.2);
            border-radius: 999px;
        }
        ::-webkit-scrollbar-thumb:hover { background: rgba(148,163,184,0.35); }

        /* ── responsive ───────────────────────────────────────── */
        @media (max-width: 780px) {
            .block-container { padding-left: 1rem !important; padding-right: 1rem !important; }
            .page-title { font-size: 1.3rem; }
            .glass-card { padding: 16px 18px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ─── sidebar navigation ────────────────────────────────────────────────────────

def render_sidebar_navigation() -> str:
    pages = ["Upload PDF", "Auto Upload", "History", "Download Info"]
    selected_page = st.session_state.get("selected_page", pages[0])

    with st.sidebar:
        st.markdown(
            """
            <div class="sb-brand">
                <div class="sb-brand-name">PO Info Extractor</div>
                <div class="sb-brand-sub">PDF Extraction Tool</div>
            </div>
            <div class="sb-nav-label">Navigation</div>
            """,
            unsafe_allow_html=True,
        )

        for page in pages:
            if st.button(page, use_container_width=True, key=f"nav_{page}"):
                selected_page = page

        st.markdown(
            """
            <hr class="divider"/>
            <div style="font-size:0.78rem; color:var(--text-subtle); line-height:1.55; margin-top:0.5rem;">
                Drop PDFs into <code>incoming_pdfs/</code> and use <strong style="color:var(--text-muted)">Auto Upload</strong> for batch processing.
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.session_state["selected_page"] = selected_page
    return selected_page


# ─── page headings ─────────────────────────────────────────────────────────────

def page_heading(eyebrow: str, title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="page-heading">
            <div class="page-eyebrow">{html.escape(eyebrow)}</div>
            <h1 class="page-title">{html.escape(title)}</h1>
            <p class="page-subtitle">{html.escape(subtitle)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─── upload page ───────────────────────────────────────────────────────────────

def render_upload_card():
    page_heading(
        "Manual Processing",
        "Upload Purchase Orders",
        "Select one or more PO PDFs and extract structured data including headers, line items, and billing details.",
    )

    st.markdown(
        """
        <div class="upload-card">
            <div class="upload-card-header">
                <div class="upload-card-label">Step 1 — Select files</div>
                <div class="upload-card-title">Drag and drop your purchase order PDFs</div>
                <div class="upload-card-hint">Accepts .pdf files only &nbsp;·&nbsp; Scanned PDFs require OCR pre-processing</div>
            </div>
            <div class="upload-card-body">
        """,
        unsafe_allow_html=True,
    )

    uploaded_files = st.file_uploader(
        "Purchase Order PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    st.markdown("</div></div>", unsafe_allow_html=True)

    if uploaded_files:
        selected_df = pd.DataFrame(
            {
                "File Name": [f.name for f in uploaded_files],
                "Size": [f"{round(len(f.getvalue()) / 1024, 1)} KB" for f in uploaded_files],
            }
        )
        st.markdown('<div class="compact-label">Selected files</div>', unsafe_allow_html=True)
        st.dataframe(selected_df, use_container_width=True, hide_index=True)

    action_col, cache_col, _ = st.columns([1.3, 1.1, 3.5])
    with action_col:
        process_clicked = st.button(
            "Start Extraction",
            type="primary",
            use_container_width=True,
            disabled=not uploaded_files,
        )
    with cache_col:
        if st.button("Clear Cache", use_container_width=True):
            ok, message = reset_persistent_vector_database()
            if ok:
                st.success(message)
            else:
                st.error(message)

    return uploaded_files, process_clicked


# ─── result tabs ───────────────────────────────────────────────────────────────

def render_overview_tab(data_df: pd.DataFrame, summary: dict) -> None:
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Files Processed", summary.get("total_files", 0))
    with m2:
        st.metric("Completed", summary.get("completed_count", 0))
    with m3:
        st.metric("Needs Review", summary.get("review_count", 0))
    with m4:
        st.metric("Line Items", summary.get("total_line_items", 0))

    overview_columns = [
        "file_name", "po_date", "buyer_name",
        "billing_state", "billing_gst_number", "extraction_status",
    ]
    available_columns = [c for c in overview_columns if c in data_df.columns]

    st.markdown(
        """
        <div class="glass-card" style="margin-top:1rem;">
            <div class="glass-card-title">Extraction Overview</div>
            <div class="glass-card-copy">Quick review of processed files, buyer location, GST details, and extraction status.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.dataframe(
        dataframe_with_status(friendly_dataframe(data_df[available_columns])),
        use_container_width=True,
        hide_index=True,
    )


def render_data_tab(data_df: pd.DataFrame) -> None:
    detail_columns = [
        "file_name", "po_number", "po_date", "buyer_name",
        "billing_address", "billing_state", "billing_pincode", "billing_gst_number",
    ]
    display_columns = [c for c in detail_columns if c in data_df.columns]

    st.markdown(
        """
        <div class="glass-card" style="margin-top:0.5rem;">
            <div class="glass-card-title">PO Header Data</div>
            <div class="glass-card-copy">Header-level purchase order details extracted from each PDF.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.dataframe(
        dataframe_with_status(friendly_dataframe(data_df[display_columns])),
        use_container_width=True,
        hide_index=True,
    )


def render_items_tab(items_df: pd.DataFrame) -> None:
    display_df = remove_empty_columns(items_df.copy())

    st.markdown(
        """
        <div class="glass-card" style="margin-top:0.5rem;">
            <div class="glass-card-title">Line Items</div>
            <div class="glass-card-copy">Item-level descriptions, quantities, prices, tax, and totals.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if display_df.empty:
        st.info("No line items were extracted.")
        return

    search = ""
    if "item_description" in display_df.columns:
        search = st.text_input(
            "Search item description",
            key="item_search",
            placeholder="Filter by description keyword…",
        )
    if search and "item_description" in display_df.columns:
        display_df = display_df[
            display_df["item_description"].fillna("").str.contains(search, case=False, na=False)
        ]

    formatted = format_numeric_columns(display_df, ["quantity", "unit_price", "tax_percent", "line_total"])
    formatted = friendly_dataframe(formatted)
    styler = formatted.style.format(
        {"Quantity": "{:,.2f}", "Unit Price": "{:,.2f}", "Tax %": "{:,.2f}", "Line Total": "{:,.2f}"},
        na_rep="",
    )
    st.dataframe(styler, use_container_width=True, hide_index=True)


def render_filewise_tab(documents: list[dict]) -> None:
    st.markdown(
        """
        <div class="glass-card" style="margin-top:0.5rem;">
            <div class="glass-card-title">File-wise Review</div>
            <div class="glass-card-copy">Expand each PDF to inspect its extracted billing, PO, and item data.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    billing_order = [
        ("Buyer Name", "buyer_name"),
        ("Billing Address", "billing_address"),
        ("State", "billing_state"),
        ("Pincode", "billing_pincode"),
        ("GST Number", "billing_gst_number"),
    ]
    other_order = [
        ("PO Number", "po_number"),
        ("PO Date", "po_date"),
    ]

    for index, document in enumerate(documents, start=1):
        data = document.get("data", {})
        file_name = document.get("file_name") or f"document_{index}"
        file_base = Path(file_name).stem
        debug = document.get("debug") or {}
        ex_status = debug.get("extraction_status", "")
        badge_html = status_badge(ex_status) if ex_status else ""

        with st.expander(f"{file_name}  {badge_html}", expanded=(index == 1)):
            col_l, col_r = st.columns(2)
            with col_l:
                st.markdown('<div class="compact-label">Billing Information</div>', unsafe_allow_html=True)
                billing_df = pd.DataFrame([{"Field": label, "Value": data.get(key)} for label, key in billing_order])
                st.dataframe(billing_df, use_container_width=True, hide_index=True)
            with col_r:
                st.markdown('<div class="compact-label">Purchase Order Details</div>', unsafe_allow_html=True)
                other_df = pd.DataFrame([{"Field": label, "Value": data.get(key)} for label, key in other_order])
                st.dataframe(other_df, use_container_width=True, hide_index=True)

            st.markdown('<div class="compact-label">Line Items</div>', unsafe_allow_html=True)
            file_items = document.get("items") or []
            if file_items:
                item_df = friendly_dataframe(remove_empty_columns(pd.DataFrame(file_items)))
                st.dataframe(item_df, use_container_width=True, hide_index=True)
            else:
                st.info("No line items found for this file.")

            st.markdown('<div class="compact-label">Download extracted data for this file</div>', unsafe_allow_html=True)
            _, file_items_df, file_data_csv, file_items_csv, file_json = build_file_export_assets(document)
            dl1, dl2, dl3 = st.columns(3)
            with dl1:
                st.download_button(
                    "Download Header CSV",
                    data=file_data_csv,
                    file_name=f"{file_base}_po_data.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            with dl2:
                if not file_items_df.empty:
                    st.download_button(
                        "Download Items CSV",
                        data=file_items_csv,
                        file_name=f"{file_base}_po_items.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                else:
                    st.caption("No item rows to download.")
            with dl3:
                st.download_button(
                    "Download JSON",
                    data=file_json,
                    file_name=f"{file_base}.json",
                    mime="application/json",
                    use_container_width=True,
                )


def render_extraction_results(api_payload: dict, total_files: int | None = None, persist_exports: bool = True) -> None:
    documents, data_df, items_df, data_csv_bytes, items_csv_bytes, json_bytes = build_export_assets(api_payload)
    item_rows = [item for d in documents for item in d.get("items", [])]

    if persist_exports:
        (OUTPUT_DIR / "po_data.csv").write_bytes(data_csv_bytes)
        (OUTPUT_DIR / "po_items.csv").write_bytes(items_csv_bytes)
        (OUTPUT_DIR / "all_extractions.json").write_bytes(json_bytes)

    completed_count = sum(1 for d in documents if d.get("debug", {}).get("extraction_status") == "Completed")
    review_count = sum(1 for d in documents if d.get("debug", {}).get("extraction_status") in {"Needs review", "Failed"})
    summary = {
        "total_files":      total_files if total_files is not None else len(documents),
        "completed_count":  completed_count,
        "review_count":     review_count,
        "total_line_items": len(item_rows),
    }

    if data_df.empty and not documents:
        st.info("No extraction results are available yet.")
        return

    overview_tab, data_tab, items_tab, filewise_tab = st.tabs(
        ["Overview", "PO Data", "Line Items", "File-wise Review"]
    )

    with overview_tab:
        render_overview_tab(data_df, summary)
    with data_tab:
        render_data_tab(data_df)
    with items_tab:
        render_items_tab(items_df)
    with filewise_tab:
        render_filewise_tab(documents)


# ─── history page ──────────────────────────────────────────────────────────────

def render_upload_history_tab(history_df: pd.DataFrame) -> None:
    page_heading(
        "Records",
        "Upload History",
        "Review previously processed purchase orders and their extraction status.",
    )

    if history_df.empty:
        st.markdown(
            """
            <div class="empty-state">
                <div class="empty-state-icon">&#x1F4C4;</div>
                <div class="empty-state-title">No history yet</div>
                <div class="empty-state-copy">Process a PDF first and it will appear here.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    if "Status" in history_df.columns:
        status_counts = history_df["Status"].value_counts()
        color_map = {
            "Extracted":    ("rgba(16,185,129,0.15)", "rgba(52,211,153,0.3)", "#34d399"),
            "Needs Review": ("rgba(245,158,11,0.15)", "rgba(251,191,36,0.3)", "#fbbf24"),
            "Failed":       ("rgba(239,68,68,0.15)",  "rgba(248,113,113,0.3)", "#f87171"),
        }
        chips_html = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:1rem;">'
        for status, count in status_counts.items():
            bg, border, color = color_map.get(status, ("rgba(148,163,184,0.1)", "rgba(148,163,184,0.25)", "#94a3b8"))
            chips_html += (
                f'<span style="background:{bg};border:1px solid {border};color:{color};'
                f'border-radius:999px;font-size:0.76rem;font-weight:700;padding:4px 12px;">'
                f'{html.escape(str(status))}&nbsp;{count}</span>'
            )
        chips_html += "</div>"
        st.markdown(chips_html, unsafe_allow_html=True)

    search_col, status_col, refresh_col = st.columns([2.4, 1.3, 1])
    with search_col:
        search = st.text_input(
            "Search",
            placeholder="Search by file name, PO number, or buyer…",
            key="history_search",
            label_visibility="collapsed",
        )
    with status_col:
        statuses = sorted(s for s in history_df["Status"].dropna().unique() if s)
        selected_status = st.selectbox(
            "Status", ["All statuses"] + statuses,
            key="history_status", label_visibility="collapsed",
        )
    with refresh_col:
        st.markdown('<div style="min-height:1.6rem;"></div>', unsafe_allow_html=True)
        if st.button("Refresh", use_container_width=True):
            st.rerun()

    display_df = history_df.copy()
    if search:
        search_text = search.lower()
        searchable = (
            display_df["File Name"].fillna("").astype(str)
            + " "
            + display_df["PO Number"].fillna("").astype(str)
            + " "
            + display_df["Buyer Name"].fillna("").astype(str)
        ).str.lower()
        display_df = display_df[searchable.str.contains(search_text, na=False)]
    if selected_status != "All statuses":
        display_df = display_df[display_df["Status"] == selected_status]

    if display_df.empty:
        st.info("No records matched your filters.")
        return

    st.dataframe(dataframe_with_status(display_df), use_container_width=True, hide_index=True)


# ─── download info page ────────────────────────────────────────────────────────

def _collect_po_download_items(api_payload: dict | None) -> list[dict]:
    """
    Return a flat list of {po_name, file_name, data_csv, items_csv, json_bytes}
    sourced from session payload or saved history.
    """
    items: list[dict] = []

    # 1. Current session documents
    if api_payload and api_payload.get("documents"):
        for document in api_payload["documents"]:
            file_name = document.get("file_name") or "extracted_file"
            file_base = Path(file_name).stem
            data = document.get("data") or {}
            po_number = data.get("po_number") or "—"
            _, _, data_csv, items_csv, json_bytes = build_file_export_assets(document)
            items.append(
                {
                    "po_name": po_number,
                    "file_name": file_name,
                    "file_base": file_base,
                    "data_csv": data_csv,
                    "items_csv": items_csv,
                    "json_bytes": json_bytes,
                }
            )
        return items

    # 2. Saved history JSON
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = []
        if isinstance(history, list):
            for document in history:
                if not isinstance(document, dict):
                    continue
                file_name = document.get("file_name") or "extracted_file"
                file_base = Path(file_name).stem
                data = document.get("data") or {}
                po_number = data.get("po_number") or "—"
                doc_items = document.get("items") or []
                data_rows = [{"file_name": file_name, **data}]
                data_df = pd.DataFrame(data_rows)
                items_df = remove_empty_columns(pd.DataFrame(doc_items)) if doc_items else pd.DataFrame()
                data_csv = data_df.to_csv(index=False).encode("utf-8")
                items_csv = items_df.to_csv(index=False).encode("utf-8") if not items_df.empty else b""
                json_bytes_single = json.dumps(
                    {"file_name": file_name, "data": data, "items": doc_items}, indent=2
                ).encode("utf-8")
                items.append(
                    {
                        "po_name": po_number,
                        "file_name": file_name,
                        "file_base": file_base,
                        "data_csv": data_csv,
                        "items_csv": items_csv,
                        "json_bytes": json_bytes_single,
                    }
                )
        if items:
            return items

    return items


def render_download_info_page() -> None:
    page_heading(
        "Exports",
        "Download Extracted Data",
        "Download individual PO files or the combined export for all processed purchase orders.",
    )

    api_payload = st.session_state.get("api_payload")
    po_items = _collect_po_download_items(api_payload)

    # ── per-PO download table ─────────────────────────────────────────────────
    if po_items:
        st.markdown(
            """
            <div class="glass-card">
                <div class="glass-card-title">Purchase Orders</div>
                <div class="glass-card-copy">Download the extracted data for each processed PO individually.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        for entry in po_items:
            col_name, col_csv, col_items, col_json = st.columns([3, 1.2, 1.2, 1.2])
            with col_name:
                st.markdown(
                    f"""
                    <div style="padding:8px 0;">
                        <div style="font-size:0.9rem;font-weight:700;color:var(--text-main);">{html.escape(entry["po_name"])}</div>
                        <div style="font-size:0.78rem;color:var(--text-muted);">{html.escape(entry["file_name"])}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with col_csv:
                st.download_button(
                    "Header CSV",
                    data=entry["data_csv"],
                    file_name=f"{entry['file_base']}_po_data.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key=f"dl_csv_{entry['file_base']}",
                )
            with col_items:
                if entry["items_csv"]:
                    st.download_button(
                        "Items CSV",
                        data=entry["items_csv"],
                        file_name=f"{entry['file_base']}_items.csv",
                        mime="text/csv",
                        use_container_width=True,
                        key=f"dl_items_{entry['file_base']}",
                    )
                else:
                    st.caption("No items")
            with col_json:
                st.download_button(
                    "JSON",
                    data=entry["json_bytes"],
                    file_name=f"{entry['file_base']}.json",
                    mime="application/json",
                    use_container_width=True,
                    key=f"dl_json_{entry['file_base']}",
                )

        st.markdown('<hr class="divider"/>', unsafe_allow_html=True)

    # ── combined export (if output files exist) ───────────────────────────────
    data_csv_path = OUTPUT_DIR / "po_data.csv"
    items_csv_path = OUTPUT_DIR / "po_items.csv"
    all_json_path = OUTPUT_DIR / "all_extractions.json"

    if data_csv_path.exists() and items_csv_path.exists() and all_json_path.exists():
        st.markdown(
            """
            <div class="glass-card" style="margin-top:0.5rem;">
                <div class="glass-card-title">Combined Export</div>
                <div class="glass-card-copy">All extracted POs combined into a single file set, saved in the <code>outputs/</code> folder.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(
                """
                <div class="export-glass-card">
                    <div class="export-type-tag">CSV</div>
                    <div class="export-card-title">PO Header Data</div>
                    <div class="export-card-copy">One row per purchase order with all header-level fields.</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.download_button(
                "Download PO Data CSV",
                data=data_csv_path.read_bytes(),
                file_name="po_data.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with col2:
            st.markdown(
                """
                <div class="export-glass-card">
                    <div class="export-type-tag">CSV</div>
                    <div class="export-card-title">Line Items</div>
                    <div class="export-card-copy">All extracted line-item rows from every processed PDF.</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.download_button(
                "Download Items CSV",
                data=items_csv_path.read_bytes(),
                file_name="po_items.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with col3:
            st.markdown(
                """
                <div class="export-glass-card">
                    <div class="export-type-tag">JSON</div>
                    <div class="export-card-title">Full Extraction JSON</div>
                    <div class="export-card-copy">Complete structured output for all POs including debug info.</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.download_button(
                "Download JSON",
                data=all_json_path.read_bytes(),
                file_name="all_extractions.json",
                mime="application/json",
                use_container_width=True,
            )
    elif not po_items:
        st.markdown(
            """
            <div class="empty-state">
                <div class="empty-state-icon">&#x1F4C2;</div>
                <div class="empty-state-title">No exports ready</div>
                <div class="empty-state-copy">Process one or more PDFs first, then return here to download the extracted data.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ─── auto upload page ──────────────────────────────────────────────────────────

def unique_destination(folder: Path, source_path: Path) -> Path:
    destination = folder / source_path.name
    if not destination.exists():
        return destination
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    return folder / f"{source_path.stem}_{suffix}{source_path.suffix}"


def extraction_failed(response: dict) -> bool:
    documents = response.get("documents") or []
    if not documents:
        return True
    for document in documents:
        debug = document.get("debug") or {}
        if debug.get("error") or debug.get("extraction_status") == "Failed":
            return True
    return False


def process_incoming_pdfs_for_streamlit() -> dict:
    pending_pdfs = sorted(INCOMING_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime)
    summary = {"processed": 0, "failed": 0, "messages": [], "response": None}
    documents = []
    warnings = []

    for pdf_path in pending_pdfs:
        try:
            response = process_uploaded_pdfs([pdf_path], include_debug=True, write_outputs=False)
            documents.extend(response.get("documents") or [])
            warnings.extend(response.get("debug", {}).get("warnings") or [])
            if extraction_failed(response):
                destination = unique_destination(FAILED_DIR, pdf_path)
                write_clean_json_outputs(response, OUTPUT_DIR, source_pdf=pdf_path, moved_to="failed_pdfs")
                write_response_export_bundle(response, OUTPUT_DIR)
                shutil.move(str(pdf_path), destination)
                summary["failed"] += 1
                summary["messages"].append(f"{pdf_path.name} moved to failed_pdfs.")
                continue
            destination = unique_destination(PROCESSED_DIR, pdf_path)
            write_clean_json_outputs(response, OUTPUT_DIR, source_pdf=pdf_path, moved_to="processed_pdfs")
            write_response_export_bundle(response, OUTPUT_DIR)
            shutil.move(str(pdf_path), destination)
            summary["processed"] += 1
            summary["messages"].append(f"{pdf_path.name} moved to processed_pdfs.")
        except Exception as exc:
            if pdf_path.exists():
                shutil.move(str(pdf_path), unique_destination(FAILED_DIR, pdf_path))
            summary["failed"] += 1
            msg = f"{pdf_path.name} failed: {type(exc).__name__}: {exc}"
            warnings.append(msg)
            summary["messages"].append(msg)

    if documents:
        summary["response"] = {
            "message": f"Auto processed {len(documents)} document(s) from incoming_pdfs.",
            "status_code": 200,
            "success": True,
            "documents": documents,
            "debug": {
                "warnings": warnings,
                "database_summary": database_summary(),
                "database_save_status": {
                    "success": summary["failed"] == 0,
                    "message": f"Auto processed {summary['processed']} PDF(s); {summary['failed']} failed.",
                },
            },
        }
        write_response_export_bundle(summary["response"], OUTPUT_DIR)
    return summary


def recent_files(folder: Path, limit: int = 8) -> list[dict]:
    if not folder.exists():
        return []
    files = sorted(
        [p for p in folder.iterdir() if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [
        {
            "File": p.name,
            "Size KB": round(p.stat().st_size / 1024, 1),
            "Modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
        for p in files[:limit]
    ]


def render_watcher_panel() -> dict:
    page_heading(
        "Automation",
        "Auto Upload",
        "Place purchase order PDFs in the local incoming_pdfs/ folder and run extraction in one click.",
    )

    pending_count = len(list(INCOMING_DIR.glob("*.pdf"))) if INCOMING_DIR.exists() else 0
    processed_count = len(list(PROCESSED_DIR.glob("*.pdf"))) if PROCESSED_DIR.exists() else 0
    failed_count = len(list(FAILED_DIR.glob("*.pdf"))) if FAILED_DIR.exists() else 0
    output_count = len(list(OUTPUT_DIR.glob("*.json"))) if OUTPUT_DIR.exists() else 0

    action_col, refresh_col, _ = st.columns([1.5, 1.2, 3.5])
    with action_col:
        start_clicked = st.button(
            "Start Auto Extraction",
            type="primary",
            use_container_width=True,
            disabled=pending_count == 0,
        )
    with refresh_col:
        if st.button("Refresh Status", use_container_width=True):
            st.rerun()

    auto_process_summary = {"processed": 0, "failed": 0, "messages": [], "response": None}
    if start_clicked:
        with st.spinner("Processing purchase orders from the local folder…"):
            auto_process_summary = process_incoming_pdfs_for_streamlit()
        processed = auto_process_summary["processed"]
        failed = auto_process_summary["failed"]
        if processed or failed:
            if failed == 0:
                st.success(f"Processed {processed} PDF(s) successfully.")
            else:
                st.warning(f"Processed {processed} PDF(s); {failed} failed — check failed_pdfs folder.")
        for message in auto_process_summary.get("messages", []):
            st.caption(f"  {message}")

    # Folder status cards
    card_data = [
        ("New PDFs",     "Waiting for extraction",    pending_count,   "folder-count-blue" if pending_count > 0 else "folder-count-green"),
        ("Processed",    "Extracted successfully",    processed_count, "folder-count-green"),
        ("Failed",       "Need manual review",        failed_count,    "folder-count-warn" if failed_count > 0 else "folder-count-green"),
        ("Output Files", "JSON/CSV results saved",    output_count,    "folder-count-cyan"),
    ]
    cols = st.columns(4)
    for col, (title, desc, count, count_class) in zip(cols, card_data):
        with col:
            st.markdown(
                f"""
                <div class="folder-glass-card">
                    <div class="folder-count {count_class}">{count}</div>
                    <div class="folder-card-title">{html.escape(title)}</div>
                    <div class="folder-card-copy">{html.escape(desc)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    incoming_rows = recent_files(INCOMING_DIR)
    st.markdown('<div class="compact-label" style="margin-top:1.4rem;">New PDFs waiting in incoming_pdfs/</div>', unsafe_allow_html=True)
    if incoming_rows:
        st.dataframe(
            pd.DataFrame(incoming_rows).rename(columns={"File": "File Name", "Size KB": "Size (KB)"}),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.markdown(
            """
            <div class="empty-state" style="padding:1.8rem;">
                <div class="empty-state-title">Folder is empty</div>
                <div class="empty-state-copy">No new PDFs are waiting. Drop files into <code>incoming_pdfs/</code> to get started.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    return auto_process_summary


# ─── export builder helpers ────────────────────────────────────────────────────

def build_export_assets(api_payload: dict) -> tuple[list[dict], pd.DataFrame, pd.DataFrame, bytes, bytes, bytes]:
    documents: list[dict] = api_payload.get("documents", [])
    clean_documents = [
        {"file_name": d.get("file_name"), "data": d.get("data", {}), "items": d.get("items", [])}
        for d in documents
    ]
    data_rows = [{"file_name": d.get("file_name"), **d.get("data", {})} for d in documents]
    item_rows = [item for d in documents for item in d.get("items", [])]
    data_df = pd.DataFrame(data_rows)
    items_df = remove_empty_columns(pd.DataFrame(item_rows))
    data_csv_bytes = data_df.to_csv(index=False).encode("utf-8")
    items_csv_bytes = items_df.to_csv(index=False).encode("utf-8")
    json_bytes = json.dumps(
        {
            "message": api_payload.get("message"),
            "status_code": api_payload.get("status_code"),
            "success": api_payload.get("success"),
            "documents": clean_documents,
        },
        indent=2,
    ).encode("utf-8")
    return documents, data_df, items_df, data_csv_bytes, items_csv_bytes, json_bytes


def build_file_export_assets(document: dict) -> tuple[pd.DataFrame, pd.DataFrame, bytes, bytes, bytes]:
    file_name = document.get("file_name") or "extracted_file"
    data_rows = [{"file_name": file_name, **(document.get("data") or {})}]
    item_rows = document.get("items") or []
    data_df = pd.DataFrame(data_rows)
    items_df = remove_empty_columns(pd.DataFrame(item_rows))
    data_csv_bytes = data_df.to_csv(index=False).encode("utf-8")
    items_csv_bytes = items_df.to_csv(index=False).encode("utf-8")
    json_bytes = json.dumps(
        {"file_name": file_name, "data": document.get("data") or {}, "items": item_rows}, indent=2
    ).encode("utf-8")
    return data_df, items_df, data_csv_bytes, items_csv_bytes, json_bytes


# ─── app entry point ───────────────────────────────────────────────────────────

st.set_page_config(
    page_title="PO Info Extractor",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

load_custom_css()
selected_page = render_sidebar_navigation()

# ── Auto Upload ────────────────────────────────────────────────────────────────
if selected_page == "Auto Upload":
    auto_process_summary = render_watcher_panel()
    auto_payload = auto_process_summary.get("response")
    if auto_payload:
        st.session_state["api_payload"] = auto_payload
        st.session_state["processed_file_names"] = ("incoming_pdfs",)
        st.session_state["payload_source"] = "incoming_pdfs"
        st.session_state["auto_upload_payload"] = auto_payload
        st.markdown(
            '<div class="compact-label" style="margin-top:1.5rem;font-size:0.9rem;">Extracted Results</div>',
            unsafe_allow_html=True,
        )
        render_extraction_results(
            auto_payload,
            total_files=len(auto_payload.get("documents", [])),
            persist_exports=False,
        )
    elif st.session_state.get("auto_upload_payload"):
        st.markdown(
            '<div class="compact-label" style="margin-top:1.5rem;font-size:0.9rem;">Last Auto Upload Results</div>',
            unsafe_allow_html=True,
        )
        render_extraction_results(
            st.session_state["auto_upload_payload"],
            total_files=len(st.session_state["auto_upload_payload"].get("documents", [])),
            persist_exports=False,
        )
    st.stop()

# ── History ────────────────────────────────────────────────────────────────────
if selected_page == "History":
    existing_documents = st.session_state.get("api_payload", {}).get("documents", [])
    history_df = build_upload_history_df(existing_documents, st.session_state.get("payload_source"))
    render_upload_history_tab(history_df)
    st.stop()

# ── Download Info ──────────────────────────────────────────────────────────────
if selected_page == "Download Info":
    render_download_info_page()
    st.stop()

# ── Upload PDF ─────────────────────────────────────────────────────────────────
uploaded_files, process_clicked = render_upload_card()

if not uploaded_files:
    st.stop()

elif not process_clicked:
    current_file_names = tuple(f.name for f in uploaded_files)
    processed_file_names = st.session_state.get("processed_file_names")
    if "api_payload" not in st.session_state or processed_file_names != current_file_names:
        st.markdown(
            """
            <div class="glass-card" style="margin-top:1rem;">
                <div class="glass-card-title">Ready to extract</div>
                <div class="glass-card-copy">Click <strong>Start Extraction</strong> to extract and validate the selected purchase orders.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()
    api_payload = st.session_state["api_payload"]

else:
    progress_bar = st.progress(0)
    status_text = st.empty()
    status_text.info(f"Preparing {len(uploaded_files)} document(s) for processing…")
    with st.spinner("Extracting purchase order data…"):
        api_ok, api_payload = process_files_for_streamlit(uploaded_files)
    progress_bar.progress(1.0)

    if not api_ok:
        for warning in api_payload.get("debug", {}).get("warnings", [api_payload.get("message", "Processing failed.")]):
            st.error(warning)
        st.stop()

    st.session_state["api_payload"] = api_payload
    st.session_state["processed_file_names"] = tuple(f.name for f in uploaded_files)
    st.session_state["payload_source"] = "manual_upload"
    status_text.success(f"Extraction complete — {len(uploaded_files)} document(s) processed.")

render_extraction_results(api_payload, total_files=len(uploaded_files), persist_exports=True)