import hashlib
import html
import json
import shutil
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from utils.chunker import split_text_into_chunks
from utils.database import (
    MYSQL_CONFIG,
    get_database_counts,
    get_latest_records,
    save_extraction_to_mysql,
)
from utils.extractor import (
    build_header_dataframe,
    build_items_dataframe,
    extract_header_fields,
    extract_item_tables,
)
from utils.pdf_reader import extract_text_from_pdf
from utils.vector_store import LocalVectorStore


APP_TITLE = "PO Info Extractor"
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
CHROMA_DIR = BASE_DIR / "chroma_db"
CHROMA_TMP_DIR = BASE_DIR / "chroma_tmp"
RAG_QUERY = "PO date billing address buyer GST bill to GST"
DEFAULT_CONTEXT_COUNT = 5


UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
CHROMA_DIR.mkdir(exist_ok=True)
CHROMA_TMP_DIR.mkdir(exist_ok=True)


@st.cache_resource(show_spinner="Loading local embedding model...")
def get_vector_store() -> LocalVectorStore:
    """Load the embedding model once so the app stays fast after first use."""
    return LocalVectorStore(persist_dir=CHROMA_DIR)


def safe_filename(filename: str) -> str:
    """Create a simple safe filename for saving uploads locally."""
    keep = []
    for char in filename:
        keep.append(char if char.isalnum() or char in (".", "-", "_") else "_")
    return "".join(keep)


def collection_name_for_file(file_bytes: bytes, unique_suffix: str = "") -> str:
    """Chroma collection names need to be short and URL-safe."""
    digest = hashlib.md5(file_bytes).hexdigest()[:16]
    suffix = f"_{unique_suffix}" if unique_suffix else ""
    return f"po_{digest}{suffix}"[:63].strip("_")


def build_csv_bytes(result_rows: list[dict[str, str]]) -> bytes:
    """Convert extraction result to downloadable CSV bytes."""
    df = pd.DataFrame(result_rows)
    return df.to_csv(index=False).encode("utf-8")


def json_safe_distance(distance: object) -> float | None:
    """Convert Chroma distance values to plain JSON-friendly floats."""
    if distance is None:
        return None
    try:
        return float(distance)
    except (TypeError, ValueError):
        return None


def format_retrieved_context(retrieved_rows: list[dict]) -> list[dict]:
    """Convert retrieved rows into JSON-friendly source context entries."""
    return [
        {
            "rank": idx,
            "distance": json_safe_distance(row.get("distance")),
            "metadata": row.get("metadata", {}),
            "text": row.get("text", ""),
        }
        for idx, row in enumerate(retrieved_rows, start=1)
    ]


def save_json_result(file_name: str, payload: dict) -> Path:
    """Save one JSON file per uploaded PDF using the original PDF name."""
    json_output_path = OUTPUT_DIR / f"{Path(safe_filename(file_name)).stem}.json"
    json_output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return json_output_path


def reset_persistent_vector_database() -> tuple[bool, str]:
    """Safely remove ChromaDB cache folders."""
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


def process_uploaded_pdf(uploaded_file, vector_store: LocalVectorStore, run_id: str, file_index: int) -> dict:
    """Process one PDF and return a row, details, and JSON payload."""
    step = "starting"
    logs: list[str] = []
    file_bytes = uploaded_file.getvalue()
    saved_path = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_filename(uploaded_file.name)}"

    def log_step(current_step: str) -> None:
        logs.append(f"{datetime.now().isoformat(timespec='seconds')} | {uploaded_file.name} | {current_step}")

    base_payload = {
        "file_name": uploaded_file.name,
        "saved_pdf_path": str(saved_path),
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "po_date": None,
        "po_number": None,
        "buyer_name": None,
        "billing_address": None,
        "billing_gst_number": None,
        "vendor_name": None,
        "vendor_gst_number": None,
        "total_amount": None,
        "items": [],
        "extraction_status": "Failed",
        "warnings": [],
        "error": None,
        "failed_step": None,
        "logs": logs,
        "debug": {},
        "retrieved_context": [],
    }

    try:
        step = "save upload"
        log_step(step)
        saved_path.write_bytes(file_bytes)
        base_payload["saved_pdf_path"] = str(saved_path)

        step = "extract PDF text"
        log_step(step)
        pdf_text = extract_text_from_pdf(saved_path)
        if not pdf_text.strip():
            raise ValueError(
                "No selectable text was found. This may be a scanned or image-only PDF."
            )

        step = "split text into chunks"
        log_step(step)
        chunks = split_text_into_chunks(pdf_text)
        if not chunks:
            raise ValueError("Text was found, but it could not be split into searchable sections.")

        step = "create isolated vector collection"
        log_step(step)
        collection_name = collection_name_for_file(file_bytes, f"{run_id}_{file_index}")
        vector_store.add_chunks(collection_name, chunks, uploaded_file.name)

        step = "retrieve source context"
        log_step(step)
        retrieved_rows = vector_store.query(collection_name, RAG_QUERY, top_k=DEFAULT_CONTEXT_COUNT)
        retrieved_contexts = [row["text"] for row in retrieved_rows]

        step = "extract PO header fields"
        log_step(step)
        header_row = extract_header_fields(uploaded_file.name, pdf_text, retrieved_contexts)

        step = "extract PO line items"
        log_step(step)
        item_rows = extract_item_tables(
            saved_path,
            pdf_text,
            file_name=uploaded_file.name,
            po_number=header_row.get("po_number"),
        )

        warnings = [warning for warning in header_row.get("warnings", "").split("; ") if warning]
        if not item_rows:
            warnings.append("Line items not found.")
        status = "Completed" if not warnings else "Needs review"
        header_row["extraction_status"] = status
        header_row["warnings"] = "; ".join(warnings)

        payload = {
            **base_payload,
            "po_number": header_row.get("po_number") or None,
            "po_date": header_row.get("po_date") or None,
            "buyer_name": header_row.get("buyer_name") or None,
            "billing_address": header_row.get("billing_address") or None,
            "billing_gst_number": header_row.get("billing_gst_number") or None,
            "vendor_name": header_row.get("vendor_name") or None,
            "vendor_gst_number": header_row.get("vendor_gst_number") or None,
            "total_amount": header_row.get("total_amount") or None,
            "items": item_rows,
            "extraction_status": status,
            "warnings": warnings,
            "failed_step": None,
            "logs": logs,
            "debug": header_row.get("debug", {}),
            "retrieved_context": format_retrieved_context(retrieved_rows),
        }
        step = "save individual JSON"
        log_step(step)
        json_output_path = save_json_result(uploaded_file.name, payload)

        return {
            "header_row": header_row,
            "item_rows": item_rows,
            "payload": payload,
            "json_output_path": json_output_path,
            "retrieved_rows": retrieved_rows,
            "error": None,
        }

    except Exception as exc:
        message = f"{uploaded_file.name} failed during {step}: {type(exc).__name__}: {exc}"
        logs.append(f"{datetime.now().isoformat(timespec='seconds')} | {message}")
        payload = {
            **base_payload,
            "extraction_status": "Failed",
            "warnings": [message],
            "error": message,
            "failed_step": step,
            "logs": logs,
            "traceback": traceback.format_exc(),
        }
        json_output_path = save_json_result(uploaded_file.name, payload)
        header_row = {
            "file_name": uploaded_file.name,
            "po_number": "",
            "po_date": "",
            "buyer_name": "",
            "billing_address": "",
            "billing_gst_number": "",
            "vendor_name": "",
            "vendor_gst_number": "",
            "total_amount": "",
            "extraction_status": "Failed",
            "warnings": message,
        }
        return {
            "header_row": header_row,
            "item_rows": [],
            "payload": payload,
            "json_output_path": json_output_path,
            "retrieved_rows": [],
            "error": message,
        }


def status_badge(status: str) -> str:
    """Return a small HTML status badge for key-value sections."""
    css_class = {
        "Completed": "status-completed",
        "Needs review": "status-review",
        "Failed": "status-failed",
    }.get(status, "status-review")
    return f'<span class="status-badge {css_class}">{html.escape(status or "Unknown")}</span>'


def status_cell_style(value: str) -> str:
    """Color status cells in Streamlit dataframes."""
    if value == "Completed":
        return "background-color: #dcfce7; color: #166534; font-weight: 600;"
    if value == "Needs review":
        return "background-color: #ffedd5; color: #9a3412; font-weight: 600;"
    if value == "Failed":
        return "background-color: #fee2e2; color: #991b1b; font-weight: 600;"
    return ""


def dataframe_with_status(df: pd.DataFrame):
    """Apply status coloring when the dataframe has an extraction_status column."""
    if "extraction_status" not in df.columns or df.empty:
        return df
    return df.style.map(status_cell_style, subset=["extraction_status"])


def format_numeric_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Prepare numeric-looking columns for readable display without changing exports."""
    formatted = df.copy()
    for column in columns:
        if column in formatted.columns:
            formatted[column] = pd.to_numeric(formatted[column], errors="coerce")
    return formatted


def render_header() -> None:
    """Render the dashboard title and short business explanation."""
    st.markdown(
        """
        <div class="app-header">
            <h1 class="app-title">Purchase Order Intelligence Platform</h1>
            <div class="app-subtitle">Bulk PO extraction, buyer details, and line-item analysis</div>
            <div class="app-note">Upload one or more PO PDFs and review extracted data before exporting.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_upload_card():
    """Render upload/reset controls and show selected files clearly."""
    with st.container(border=True):
        st.markdown('<div class="section-title">Upload Purchase Order PDFs</div>', unsafe_allow_html=True)
        st.write("Add one or more PDF purchase orders for bulk extraction.")
        reset_col, _ = st.columns([1, 3])
        with reset_col:
            if st.button("Clear cache / reset vector database", use_container_width=True):
                ok, message = reset_persistent_vector_database()
                if ok:
                    st.success(message)
                else:
                    st.error(message)

        uploaded_files = st.file_uploader(
            "Purchase Order PDFs",
            type=["pdf"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )

        if uploaded_files:
            st.markdown(f"**Selected files:** {len(uploaded_files)}")
            selected_df = pd.DataFrame(
                {
                    "file_name": [file.name for file in uploaded_files],
                    "size_kb": [round(len(file.getvalue()) / 1024, 1) for file in uploaded_files],
                }
            )
            st.dataframe(selected_df, use_container_width=True, hide_index=True)

    return uploaded_files


def render_summary_cards(
    total_files: int,
    completed_count: int,
    review_count: int,
    failed_count: int,
    total_line_items: int,
) -> None:
    """Render high-level batch metrics."""
    cols = st.columns(5)
    cols[0].metric("Total Files", total_files)
    cols[1].metric("Successfully Extracted", completed_count)
    cols[2].metric("Needs Review", review_count)
    cols[3].metric("Failed", failed_count)
    cols[4].metric("Total Line Items", total_line_items)


def render_overview_tab(headers_df: pd.DataFrame, summary: dict[str, int]) -> None:
    """Render batch summary and compact header table."""
    render_summary_cards(
        summary["total_files"],
        summary["completed_count"],
        summary["review_count"],
        summary["failed_count"],
        summary["total_line_items"],
    )
    overview_columns = [
        "file_name",
        "po_number",
        "po_date",
        "buyer_name",
        "billing_gst_number",
        "extraction_status",
    ]
    available_columns = [column for column in overview_columns if column in headers_df.columns]
    st.markdown('<div class="section-title">Quick Review</div>', unsafe_allow_html=True)
    st.dataframe(
        dataframe_with_status(headers_df[available_columns]),
        use_container_width=True,
        hide_index=True,
    )


def render_header_tab(headers_df: pd.DataFrame) -> None:
    """Render searchable PO header data without crowding long addresses."""
    search = st.text_input("Search file name, PO number, or buyer name", key="header_search")
    statuses = ["All"] + sorted([status for status in headers_df["extraction_status"].dropna().unique()])
    status_filter = st.selectbox("Status filter", statuses, key="header_status")

    filtered = headers_df.copy()
    if search:
        search_mask = (
            filtered["file_name"].fillna("").str.contains(search, case=False, na=False)
            | filtered["po_number"].fillna("").str.contains(search, case=False, na=False)
            | filtered["buyer_name"].fillna("").str.contains(search, case=False, na=False)
        )
        filtered = filtered[search_mask]
    if status_filter != "All":
        filtered = filtered[filtered["extraction_status"] == status_filter]

    display_columns = [column for column in filtered.columns if column != "billing_address"]
    st.dataframe(
        dataframe_with_status(filtered[display_columns]),
        use_container_width=True,
        hide_index=True,
    )

    if filtered.empty:
        st.info("No PO headers match the current filters.")
        return

    selected_file = st.selectbox(
        "View billing address for",
        filtered["file_name"].tolist(),
        key="billing_address_selector",
    )
    selected_row = filtered[filtered["file_name"] == selected_file].iloc[0]
    address_display = html.escape(selected_row.get("billing_address") or "Billing address not found.")
    st.markdown(
        f"""
        <div class="section-card">
            <div class="section-title">Billing Address</div>
            <div class="address-text">{address_display}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_items_tab(items_df: pd.DataFrame) -> None:
    """Render searchable and filterable line-item data."""
    item_columns = [
        "file_name",
        "po_number",
        "item_no",
        "item_description",
        "hsn_sac",
        "quantity",
        "uom",
        "unit_price",
        "tax_percent",
        "line_total",
    ]
    display_df = items_df[[column for column in item_columns if column in items_df.columns]].copy()

    if display_df.empty:
        st.info("No line items were extracted.")
        return

    search = st.text_input("Search item description", key="item_search")
    file_options = ["All"] + sorted(display_df["file_name"].dropna().unique().tolist())
    file_filter = st.selectbox("File filter", file_options, key="item_file_filter")

    if search:
        display_df = display_df[
            display_df["item_description"].fillna("").str.contains(search, case=False, na=False)
        ]
    if file_filter != "All":
        display_df = display_df[display_df["file_name"] == file_filter]

    formatted = format_numeric_columns(display_df, ["quantity", "unit_price", "tax_percent", "line_total"])
    styler = formatted.style.format(
        {
            "quantity": "{:,.2f}",
            "unit_price": "{:,.2f}",
            "tax_percent": "{:,.2f}",
            "line_total": "{:,.2f}",
        },
        na_rep="",
    )
    st.dataframe(styler, use_container_width=True, hide_index=True)


def render_verification_tab(processed_items: list[dict]) -> None:
    """Render one non-nested expander per file for manual verification."""
    for item in processed_items:
        payload = item["payload"]
        with st.expander(f"View details for {payload['file_name']}"):
            st.markdown(
                f"**Status:** {status_badge(payload.get('extraction_status') or 'Failed')}",
                unsafe_allow_html=True,
            )

            detail_rows = [
                ("File Name", payload.get("file_name")),
                ("PO Number", payload.get("po_number")),
                ("PO Date", payload.get("po_date")),
                ("Buyer Name", payload.get("buyer_name")),
                ("Billing GST Number", payload.get("billing_gst_number")),
                ("Vendor Name", payload.get("vendor_name")),
                ("Vendor GST Number", payload.get("vendor_gst_number")),
                ("Total Amount", payload.get("total_amount")),
            ]
            detail_df = pd.DataFrame(
                [{"field": label, "value": value or "Not found"} for label, value in detail_rows]
            )
            st.dataframe(detail_df, use_container_width=True, hide_index=True)

            address_display = html.escape(payload.get("billing_address") or "Billing address not found.")
            st.markdown(
                f"""
                <div class="section-card">
                    <div class="section-title">Billing Address</div>
                    <div class="address-text">{address_display}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            st.markdown("**Extracted Line Items**")
            file_items = payload.get("items") or []
            if file_items:
                st.dataframe(build_items_dataframe(file_items), use_container_width=True, hide_index=True)
            else:
                st.write("No line items found for this file.")

            warnings = payload.get("warnings") or []
            if warnings:
                st.markdown("**Warnings**")
                for warning in warnings:
                    st.warning(warning)

            if payload.get("error"):
                st.error(payload["error"])

            if payload.get("failed_step"):
                st.markdown("**Failed Step**")
                st.code(payload["failed_step"])

            logs = payload.get("logs") or []
            if logs:
                st.markdown("**Processing Log**")
                st.code("\n".join(logs))

            st.markdown("**Retrieved Source Context**")
            source_context = payload.get("retrieved_context") or []
            if source_context:
                for context in source_context:
                    st.markdown(f"Source passage {context['rank']}")
                    st.write(context.get("text", ""))
                    if context["rank"] < len(source_context):
                        st.divider()
            else:
                st.write("No source context available for this file.")


def render_database_status_card(db_save_result: dict, db_counts: dict) -> None:
    """Render MySQL connection and save-count status."""
    connected = bool(db_save_result.get("connected")) and bool(db_counts.get("connected"))
    status = "MySQL Connected" if connected else "Not Connected"
    cols = st.columns(5)
    cols[0].metric("Database Status", status)
    cols[1].metric("Headers saved in this run", db_save_result.get("headers_saved", 0))
    cols[2].metric("Items saved in this run", db_save_result.get("items_saved", 0))
    cols[3].metric("Total headers in database", db_counts.get("headers_count", 0))
    cols[4].metric("Total items in database", db_counts.get("items_count", 0))


def render_downloads_tab(
    headers_df: pd.DataFrame,
    items_df: pd.DataFrame,
    headers_csv_bytes: bytes,
    items_csv_bytes: bytes,
    json_bytes: bytes,
    db_save_result: dict,
    db_counts: dict,
) -> None:
    """Render export buttons and saved output paths."""
    st.markdown('<div class="section-title">Export Results</div>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "Download PO Headers CSV",
            data=headers_csv_bytes,
            file_name="po_headers.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col2:
        st.download_button(
            "Download PO Items CSV",
            data=items_csv_bytes,
            file_name="po_items.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col3:
        st.download_button(
            "Download Combined JSON",
            data=json_bytes,
            file_name="all_extractions.json",
            mime="application/json",
            use_container_width=True,
        )

    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">Saved Files</div>
            <div class="address-text">outputs/po_headers.csv<br>outputs/po_items.csv<br>outputs/all_extractions.json</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-title">Database</div>', unsafe_allow_html=True)
    render_database_status_card(db_save_result, db_counts)
    config = MYSQL_CONFIG
    st.markdown(
        f"""
        <div class="section-card">
            <div class="section-title">MySQL Connection Settings</div>
            <div class="address-text">Host: {html.escape(str(config["host"]))}<br>
            Port: {html.escape(str(config["port"]))}<br>
            Database: {html.escape(str(config["database"]))}<br>
            User: {html.escape(str(config["user"]))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if db_save_result.get("success"):
        st.success(db_save_result.get("message", "Records stored successfully."))
    else:
        st.error(
            db_save_result.get(
                "message",
                "MySQL connection failed. Please check MySQL service, username, password, and database.",
            )
        )

    if st.button("View Latest Saved Records", use_container_width=True):
        latest = get_latest_records(limit=10)
        if latest["success"]:
            st.markdown("**Latest 10 po_headers rows**")
            st.dataframe(latest["headers"], use_container_width=True, hide_index=True)
            st.markdown("**Latest 10 po_items rows**")
            st.dataframe(latest["items"], use_container_width=True, hide_index=True)
        else:
            st.error(latest["message"])


st.set_page_config(page_title="Purchase Order Intelligence Platform", layout="wide")

st.markdown(
    """
    <style>
        .block-container {
            padding-top: 2.2rem;
            padding-bottom: 2.5rem;
            max-width: 1180px;
        }
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 18px 20px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
        }
        [data-testid="stMetricLabel"] {
            color: #475569;
            font-weight: 600;
        }
        [data-testid="stMetricValue"] {
            color: #0f172a;
            font-size: 1.35rem;
            line-height: 1.4;
        }
        .app-header {
            border-bottom: 1px solid #e5e7eb;
            padding-bottom: 1rem;
            margin-bottom: 1.5rem;
        }
        .app-title {
            color: #0f172a;
            font-size: 2rem;
            font-weight: 700;
            letter-spacing: 0;
            margin: 0;
        }
        .app-subtitle {
            color: #64748b;
            font-size: 1rem;
            margin-top: 0.35rem;
        }
        .app-note {
            color: #94a3b8;
            font-size: 0.92rem;
            margin-top: 0.35rem;
        }
        .section-card {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
            margin-top: 1rem;
        }
        .section-title {
            color: #0f172a;
            font-size: 1rem;
            font-weight: 700;
            margin-bottom: 0.75rem;
        }
        .address-text {
            color: #1e293b;
            white-space: pre-wrap;
            line-height: 1.6;
            font-size: 0.98rem;
        }
        .muted-note {
            color: #64748b;
            font-size: 0.9rem;
        }
        .status-badge {
            border-radius: 999px;
            display: inline-block;
            font-size: 0.84rem;
            font-weight: 700;
            padding: 4px 10px;
        }
        .status-completed {
            background: #dcfce7;
            color: #166534;
        }
        .status-review {
            background: #ffedd5;
            color: #9a3412;
        }
        .status-failed {
            background: #fee2e2;
            color: #991b1b;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

render_header()
uploaded_files = render_upload_card()

if not uploaded_files:
    st.info("Upload one or more Purchase Order PDFs to begin processing.")
    st.stop()

try:
    with st.spinner("Preparing document analysis engine..."):
        vector_store = LocalVectorStore(persist_dir=None)
except Exception as exc:
    st.error(f"Unable to load the document analysis engine: {exc}")
    st.stop()

progress_bar = st.progress(0)
progress_text = st.empty()
processed_items: list[dict] = []
run_id = datetime.now().strftime("%H%M%S%f")

for index, uploaded_file in enumerate(uploaded_files, start=1):
    progress_text.write(f"Processing {index} of {len(uploaded_files)}: {uploaded_file.name}")
    processed_items.append(process_uploaded_pdf(uploaded_file, vector_store, run_id, index))
    progress_bar.progress(index / len(uploaded_files))

progress_text.write(f"Processed {len(uploaded_files)} file(s).")

header_rows = [item["header_row"] for item in processed_items]
item_rows = [row for item in processed_items for row in item["item_rows"]]
json_payloads = [item["payload"] for item in processed_items]
headers_df = build_header_dataframe(header_rows)
items_df = build_items_dataframe(item_rows)

headers_csv_path = OUTPUT_DIR / "po_headers.csv"
items_csv_path = OUTPUT_DIR / "po_items.csv"
all_json_path = OUTPUT_DIR / "all_extractions.json"
headers_csv_bytes = headers_df.to_csv(index=False).encode("utf-8")
items_csv_bytes = items_df.to_csv(index=False).encode("utf-8")
json_bytes = json.dumps(json_payloads, indent=2).encode("utf-8")
headers_csv_path.write_bytes(headers_csv_bytes)
items_csv_path.write_bytes(items_csv_bytes)
all_json_path.write_bytes(json_bytes)

db_save_result = save_extraction_to_mysql(header_rows, item_rows)
db_counts = get_database_counts() if db_save_result.get("success") else {
    "connected": False,
    "headers_count": 0,
    "items_count": 0,
    "message": db_save_result.get("message", ""),
}

completed_count = sum(1 for row in header_rows if row["extraction_status"] == "Completed")
review_count = sum(1 for row in header_rows if row["extraction_status"] == "Needs review")
failed_count = sum(1 for row in header_rows if row["extraction_status"] == "Failed")
summary = {
    "total_files": len(uploaded_files),
    "completed_count": completed_count,
    "review_count": review_count,
    "failed_count": failed_count,
    "total_line_items": len(item_rows),
}

st.markdown('<div class="section-title">Processing Summary</div>', unsafe_allow_html=True)
render_summary_cards(
    summary["total_files"],
    summary["completed_count"],
    summary["review_count"],
    summary["failed_count"],
    summary["total_line_items"],
)

overview_tab, header_tab, items_tab, verification_tab, downloads_tab = st.tabs(
    [
        "Overview",
        "PO Header Data",
        "Line Item Data",
        "File-wise Verification",
        "Downloads",
    ]
)

with overview_tab:
    render_overview_tab(headers_df, summary)

with header_tab:
    render_header_tab(headers_df)

with items_tab:
    render_items_tab(items_df)

with verification_tab:
    render_verification_tab(processed_items)

with downloads_tab:
    render_downloads_tab(
        headers_df,
        items_df,
        headers_csv_bytes,
        items_csv_bytes,
        json_bytes,
        db_save_result,
        db_counts,
    )
