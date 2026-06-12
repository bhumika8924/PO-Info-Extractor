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
    if value in {"Completed", "Extracted"}:
        return "background-color: #dcfce7; color: #166534; font-weight: 600;"
    if value in {"Needs review", "Needs Review"}:
        return "background-color: #ffedd5; color: #9a3412; font-weight: 600;"
    if value == "Failed":
        return "background-color: #fee2e2; color: #991b1b; font-weight: 600;"
    return ""


def dataframe_with_status(df: pd.DataFrame):
    """Apply status coloring when the dataframe has an extraction_status column."""
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
    """Return a display-only dataframe with readable column labels."""
    return df.rename(columns={key: value for key, value in FRIENDLY_COLUMN_NAMES.items() if key in df.columns})


def display_history_status(value: str | None) -> str:
    """Map internal extraction statuses to history labels."""
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
    """Load display history rows from the existing JSON output history if present."""
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
    """Fetch upload history from existing database records without changing schema."""
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
    """Build a user-facing upload history from DB, saved history, and current session data."""
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
    """Prepare numeric-looking columns for readable display without changing exports."""
    formatted = df.copy()
    for column in columns:
        if column in formatted.columns:
            formatted[column] = pd.to_numeric(formatted[column], errors="coerce")
    return formatted


def check_api_status() -> dict:
    """Return local Streamlit system status."""
    return {
        "api_connected": True,
        "status": "ok",
        "database": database_status(),
    }


def process_files_for_streamlit(uploaded_files) -> tuple[bool, dict]:
    """Process selected PDFs with the shared extraction function."""
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


def render_header(status: dict) -> None:
    """Render a simple business header."""
    st.markdown(
        """
        <div class="app-hero">
            <div class="hero-copy">
                <h1 class="app-title">PO Information Extractor</h1>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_upload_card():
    """Render the upload panel."""
    st.markdown(
        """
        <div class="section-header">
            <div>
                <h2 class="panel-title">Upload Purchase Order PDFs</h2>
                <p class="panel-copy">Select one or more PDF files to extract structured PO data.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="upload-shell">
            <div class="upload-icon">PDF</div>
            <div>
                <div class="upload-title">Drag and drop purchase orders</div>
                <div class="upload-copy">Browse or drop PDF files below. Multiple files are supported.</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    uploaded_files = st.file_uploader(
        "Purchase Order PDFs",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if uploaded_files:
        selected_df = pd.DataFrame(
            {
                "File Name": [file.name for file in uploaded_files],
                "Size": [f"{round(len(file.getvalue()) / 1024, 1)} KB" for file in uploaded_files],
            }
        )
        st.markdown('<div class="compact-heading">Selected Files</div>', unsafe_allow_html=True)
        st.dataframe(selected_df, use_container_width=True, hide_index=True)

    action_col, cache_col, _ = st.columns([1.2, 1.1, 3.5])
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


def render_sidebar_navigation() -> str:
    pages = ["Upload PDF", "Auto Upload", "History", "Download Info"]
    selected_page = st.session_state.get("selected_page", pages[0])

    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-title">PO Info Extractor</div>
            <div class="sidebar-copy">Choose what you want to do.</div>
            """,
            unsafe_allow_html=True,
        )
        for page in pages:
            if st.button(page, use_container_width=True):
                selected_page = page

    st.session_state["selected_page"] = selected_page
    return selected_page

def render_overview_tab(data_df: pd.DataFrame, summary: dict[str, int]) -> None:
    """Render batch summary and compact header table."""
    overview_columns = [
        "file_name",
        "po_date",
        "buyer_name",
        "billing_state",
        "billing_gst_number",
        "extraction_status",
    ]
    available_columns = [column for column in overview_columns if column in data_df.columns]
    st.markdown(
        """
        <div class="section-card table-card">
            <div class="section-title">Overview</div>
            <div class="section-copy">A quick review of processed files, buyer location, GST details, and extraction status.</div>
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
    """Render clean PO data table."""
    detail_columns = [
        "file_name",
        "po_number",
        "po_date",
        "buyer_name",
        "billing_address",
        "billing_state",
        "billing_pincode",
        "billing_gst_number",
    ]
    display_columns = [column for column in detail_columns if column in data_df.columns]
    st.markdown(
        """
        <div class="section-card table-card">
            <div class="section-title">PO Data</div>
            <div class="section-copy">Header-level purchase order details extracted from each PDF.</div>
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
    """Render searchable line-item data without repeating document-level fields."""
    display_df = remove_empty_columns(items_df.copy())

    st.markdown(
        """
        <div class="section-card table-card">
            <div class="section-title">Line Items</div>
            <div class="section-copy">Search and review item-level descriptions, quantities, prices, tax, and totals.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if display_df.empty:
        st.info("No line items were extracted.")
        return

    if "item_description" in display_df.columns:
        search = st.text_input("Search item description", key="item_search", placeholder="Search item descriptions")
    else:
        search = ""
    if search and "item_description" in display_df.columns:
        display_df = display_df[
            display_df["item_description"].fillna("").str.contains(search, case=False, na=False)
        ]

    formatted = format_numeric_columns(display_df, ["quantity", "unit_price", "tax_percent", "line_total"])
    formatted = friendly_dataframe(formatted)
    styler = formatted.style.format(
        {
            "Quantity": "{:,.2f}",
            "Unit Price": "{:,.2f}",
            "Tax %": "{:,.2f}",
            "Line Total": "{:,.2f}",
        },
        na_rep="",
    )
    st.dataframe(styler, use_container_width=True, hide_index=True)


def render_filewise_tab(documents: list[dict]) -> None:
    """Render complete data and item table for each uploaded PDF."""
    st.markdown(
        """
        <div class="section-card table-card">
            <div class="section-title">File-wise Review</div>
            <div class="section-copy">Open each PDF result to inspect extracted billing, PO, and item data.</div>
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
        with st.expander(f"PDF {index}: {file_name}", expanded=index == 1):
            billing_df = pd.DataFrame(
                [{"Field": label, "Value": data.get(key)} for label, key in billing_order]
            )
            st.markdown("**Billing Information**")
            st.dataframe(billing_df, use_container_width=True, hide_index=True)

            other_df = pd.DataFrame(
                [{"Field": label, "Value": data.get(key)} for label, key in other_order]
            )
            st.markdown("**Purchase Order Information**")
            st.dataframe(other_df, use_container_width=True, hide_index=True)

            st.markdown("**Items**")
            file_items = document.get("items") or []
            if file_items:
                item_df = friendly_dataframe(remove_empty_columns(pd.DataFrame(file_items)))
                st.dataframe(item_df, use_container_width=True, hide_index=True)
            else:
                item_df = pd.DataFrame()
                st.write("No line items found for this file.")

            st.markdown("**Download extracted data for this file**")
            _, download_col, _ = st.columns([0.2, 9, 0.2])
            with download_col:
                _, file_items_df, file_data_csv, file_items_csv, file_json = build_file_export_assets(document)
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.download_button(
                        "Download Header CSV",
                        data=file_data_csv,
                        file_name=f"{file_base}_po_data.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                with col2:
                    if not file_items_df.empty:
                        st.download_button(
                            "Download Items CSV",
                            data=file_items_csv,
                            file_name=f"{file_base}_po_items.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )
                    else:
                        st.write("No item rows to download.")
                with col3:
                    st.download_button(
                        "Download JSON",
                        data=file_json,
                        file_name=f"{file_base}.json",
                        mime="application/json",
                        use_container_width=True,
                    )


def render_upload_history_tab(history_df: pd.DataFrame) -> None:
    """Render searchable upload and processing history."""
    st.markdown(
        """
        <div class="section-card table-card">
            <div class="section-title">Upload History</div>
            <div class="section-copy">Review PDFs that were uploaded or processed earlier.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if history_df.empty:
        st.info("No upload history found yet. Process a PDF to see it here.")
        return

    search_col, status_col, refresh_col = st.columns([2.2, 1.2, 1])
    with search_col:
        search = st.text_input(
            "Search history",
            placeholder="Search by file name, PO number, or buyer name",
            key="history_search",
        )
    with status_col:
        statuses = sorted(status for status in history_df["Status"].dropna().unique() if status)
        selected_status = st.selectbox("Filter by status", ["All"] + statuses, key="history_status")
    with refresh_col:
        st.markdown('<div class="refresh-spacer"></div>', unsafe_allow_html=True)
        if st.button("Refresh History", use_container_width=True):
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

    if selected_status != "All":
        display_df = display_df[display_df["Status"] == selected_status]

    if display_df.empty:
        st.info("No upload history matched your filters.")
        return

    st.dataframe(dataframe_with_status(display_df), use_container_width=True, hide_index=True)


def render_downloads_tab(
    data_df: pd.DataFrame,
    items_df: pd.DataFrame,
    data_csv_bytes: bytes,
    items_csv_bytes: bytes,
    json_bytes: bytes,
) -> None:
    """Render export buttons and saved output paths."""
    st.markdown(
        """
        <div class="section-card table-card">
            <div class="section-title">Download Extracted Data</div>
            <div class="section-copy">Download the generated files using the original export schema and filenames.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(
            """
            <div class="export-card">
                <div class="export-title">PO Data CSV</div>
                <div class="export-copy">Header-level purchase order details.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.download_button(
            "Download PO Data CSV",
            data=data_csv_bytes,
            file_name="po_data.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col2:
        st.markdown(
            """
            <div class="export-card">
                <div class="export-title">PO Items CSV</div>
                <div class="export-copy">Extracted line-item details.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.download_button(
            "Download PO Items CSV",
            data=items_csv_bytes,
            file_name="po_items.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col3:
        st.markdown(
            """
            <div class="export-card">
                <div class="export-title">Combined JSON</div>
                <div class="export-copy">Complete structured extraction output.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
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
            <div class="section-title">Prepared Files</div>
            <div class="address-text">PO data, PO items, and combined extraction results are ready for download.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_export_assets(api_payload: dict) -> tuple[list[dict], pd.DataFrame, pd.DataFrame, bytes, bytes, bytes]:
    """Build the same display/export assets used after extraction."""
    documents: list[dict] = api_payload.get("documents", [])
    clean_documents = [
        {
            "file_name": document.get("file_name"),
            "data": document.get("data", {}),
            "items": document.get("items", []),
        }
        for document in documents
    ]
    data_rows = [{"file_name": document.get("file_name"), **document.get("data", {})} for document in documents]
    item_rows = [
        item
        for document in documents
        for item in document.get("items", [])
    ]
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
    """Build export assets for a single extracted file."""
    file_name = document.get("file_name") or "extracted_file"
    file_base = Path(file_name).stem
    data_rows = [{"file_name": file_name, **(document.get("data") or {})}]
    item_rows = document.get("items") or []
    data_df = pd.DataFrame(data_rows)
    items_df = remove_empty_columns(pd.DataFrame(item_rows))
    data_csv_bytes = data_df.to_csv(index=False).encode("utf-8")
    items_csv_bytes = items_df.to_csv(index=False).encode("utf-8")
    json_bytes = json.dumps(
        {
            "file_name": file_name,
            "data": document.get("data") or {},
            "items": item_rows,
        },
        indent=2,
    ).encode("utf-8")
    return data_df, items_df, data_csv_bytes, items_csv_bytes, json_bytes


def render_extraction_results(api_payload: dict, total_files: int | None = None, persist_exports: bool = True) -> None:
    """Render the same result tabs for manual upload and auto upload."""
    documents, data_df, items_df, data_csv_bytes, items_csv_bytes, json_bytes = build_export_assets(api_payload)
    item_rows = [
        item
        for document in documents
        for item in document.get("items", [])
    ]

    if persist_exports:
        data_csv_path = OUTPUT_DIR / "po_data.csv"
        items_csv_path = OUTPUT_DIR / "po_items.csv"
        all_json_path = OUTPUT_DIR / "all_extractions.json"
        data_csv_path.write_bytes(data_csv_bytes)
        items_csv_path.write_bytes(items_csv_bytes)
        all_json_path.write_bytes(json_bytes)

    completed_count = sum(
        1 for document in documents if document.get("debug", {}).get("extraction_status") == "Completed"
    )
    review_count = sum(
        1 for document in documents if document.get("debug", {}).get("extraction_status") in {"Needs review", "Failed"}
    )
    summary = {
        "total_files": total_files if total_files is not None else len(documents),
        "completed_count": completed_count,
        "review_count": review_count,
        "total_line_items": len(item_rows),
    }

    if data_df.empty and not documents:
        st.info("No extraction results are available yet.")
        return

    overview_tab, data_tab, items_tab, filewise_tab = st.tabs(
        [
            "Overview",
            "PO Data",
            "Line Items",
            "File-wise Results",
        ]
    )

    with overview_tab:
        render_overview_tab(data_df, summary)

    with data_tab:
        render_data_tab(data_df)

    with items_tab:
        render_items_tab(items_df)

    with filewise_tab:
        render_filewise_tab(documents)


def load_saved_export_assets() -> tuple[pd.DataFrame, pd.DataFrame, bytes, bytes, bytes] | None:
    """Load previously generated export files when session data is not available."""
    data_csv_path = OUTPUT_DIR / "po_data.csv"
    if not data_csv_path.exists():
        data_csv_path = OUTPUT_DIR / "po_headers.csv"
    items_csv_path = OUTPUT_DIR / "po_items.csv"
    all_json_path = OUTPUT_DIR / "all_extractions.json"

    if not data_csv_path.exists() or not items_csv_path.exists() or not all_json_path.exists():
        return None

    data_csv_bytes = data_csv_path.read_bytes()
    items_csv_bytes = items_csv_path.read_bytes()
    json_bytes = all_json_path.read_bytes()
    data_df = pd.read_csv(data_csv_path)
    items_df = pd.read_csv(items_csv_path)
    return data_df, items_df, data_csv_bytes, items_csv_bytes, json_bytes


def render_download_info_page() -> None:
    """Render downloads as a standalone sidebar page."""
    api_payload = st.session_state.get("api_payload")
    if api_payload and api_payload.get("documents"):
        _, data_df, items_df, data_csv_bytes, items_csv_bytes, json_bytes = build_export_assets(api_payload)
        render_downloads_tab(data_df, items_df, data_csv_bytes, items_csv_bytes, json_bytes)
        return

    saved_exports = load_saved_export_assets()
    if saved_exports:
        data_df, items_df, data_csv_bytes, items_csv_bytes, json_bytes = saved_exports
        render_downloads_tab(data_df, items_df, data_csv_bytes, items_csv_bytes, json_bytes)
        return

    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">Download Info</div>
            <div class="address-text">No extracted files are ready yet. Process a PDF first, then return here to download the generated outputs.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.info("No download files were found yet.")


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
        if debug.get("error"):
            return True
        if debug.get("extraction_status") == "Failed":
            return True
    return False


def process_incoming_pdfs_for_streamlit() -> dict:
    """Automatically process PDFs already dropped into incoming_pdfs."""
    pending_pdfs = sorted(INCOMING_DIR.glob("*.pdf"), key=lambda path: path.stat().st_mtime)
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
                destination = unique_destination(FAILED_DIR, pdf_path)
                shutil.move(str(pdf_path), destination)
            summary["failed"] += 1
            message = f"{pdf_path.name} failed: {type(exc).__name__}: {exc}"
            warnings.append(message)
            summary["messages"].append(message)

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
                    "message": (
                        f"Auto processed {summary['processed']} PDF(s); "
                        f"{summary['failed']} failed."
                    ),
                },
            },
        }
        write_response_export_bundle(summary["response"], OUTPUT_DIR)
    return summary


def recent_files(folder: Path, limit: int = 8) -> list[dict]:
    """Return recent files from a watcher runtime folder."""
    if not folder.exists():
        return []
    files = [path for path in folder.iterdir() if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [
        {
            "File": path.name,
            "Size KB": round(path.stat().st_size / 1024, 1),
            "Modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for path in files[:limit]
    ]


def render_watcher_panel() -> dict:
    """Display folder automation status in Streamlit."""
    st.markdown(
        """
        <div class="section-card table-card">
            <div class="panel-heading">
                <div>
                    <h2 class="panel-title">Auto Upload from Folder</h2>
                    <p class="panel-copy">Place purchase order PDFs in the local input folder and the app will detect them for extraction.</p>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    pending_count = len(list(INCOMING_DIR.glob("*.pdf"))) if INCOMING_DIR.exists() else 0
    action_col, refresh_col, _ = st.columns([1.4, 1.1, 3.5])
    with action_col:
        start_clicked = st.button(
            "Start Auto Extraction",
            type="primary",
            use_container_width=True,
            disabled=pending_count == 0,
        )
    with refresh_col:
        if st.button("Refresh Folder Status", use_container_width=True):
            st.rerun()

    auto_process_summary = {"processed": 0, "failed": 0, "messages": [], "response": None}
    if start_clicked:
        with st.spinner("Processing purchase orders from the local folder..."):
            auto_process_summary = process_incoming_pdfs_for_streamlit()
        if auto_process_summary["processed"] or auto_process_summary["failed"]:
            st.success(
                f"Auto processed {auto_process_summary['processed']} PDF(s); "
                f"{auto_process_summary['failed']} failed."
            )
        for message in auto_process_summary.get("messages", []):
            st.caption(message)

    status_cards = [
        ("New PDFs", "Files waiting to be extracted", len(list(INCOMING_DIR.glob("*.pdf"))) if INCOMING_DIR.exists() else 0),
        ("Processed PDFs", "Files extracted successfully", len(list(PROCESSED_DIR.glob("*.pdf"))) if PROCESSED_DIR.exists() else 0),
        ("Failed PDFs", "Files that need review", len(list(FAILED_DIR.glob("*.pdf"))) if FAILED_DIR.exists() else 0),
        ("Exported Results", "Output files generated", len(list(OUTPUT_DIR.glob("*.json"))) if OUTPUT_DIR.exists() else 0),
    ]

    card_cols = st.columns(4)
    for col, (title, description, count) in zip(card_cols, status_cards):
        with col:
            st.markdown(
                f"""
                <div class="folder-status-card">
                    <div class="folder-count">{count}</div>
                    <div class="folder-title">{html.escape(title)}</div>
                    <div class="folder-copy">{html.escape(description)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    incoming_rows = recent_files(INCOMING_DIR)
    st.markdown('<div class="compact-heading">New PDFs Waiting</div>', unsafe_allow_html=True)
    if incoming_rows:
        st.dataframe(
            pd.DataFrame(incoming_rows).rename(columns={"File": "File Name", "Size KB": "Size"}),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No new PDFs are waiting for extraction.")

    return auto_process_summary


st.set_page_config(page_title="PO Info Extractor", layout="wide")

st.markdown(
    """
    <style>
        :root {
            --ink: #172033;
            --muted: #667085;
            --line: #e2e8f0;
            --line-strong: #cbd5e1;
            --panel: #ffffff;
            --soft: #f5f7fb;
            --brand: #1f5fbf;
            --brand-dark: #174a96;
            --accent: #0f766e;
            --success-bg: #e7f8ef;
            --success-text: #087443;
            --warn-bg: #fff4e5;
            --warn-text: #b54708;
            --danger-bg: #fee4e2;
            --danger-text: #b42318;
            --shadow: 0 16px 36px rgba(15, 23, 42, 0.09);
            --shadow-soft: 0 8px 20px rgba(15, 23, 42, 0.06);
        }
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(31, 95, 191, 0.08), transparent 30%),
                linear-gradient(180deg, #f8fafc 0%, #eef3f8 100%);
            color: var(--ink);
        }
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 3rem;
            max-width: 1240px;
        }
        [data-testid="stSidebar"] {
            background: #ffffff;
            border-right: 1px solid var(--line);
        }
        [data-testid="stSidebar"] [data-testid="stRadio"] label {
            color: var(--ink);
            font-weight: 700;
        }
        .sidebar-title {
            color: var(--ink);
            font-size: 1.15rem;
            font-weight: 850;
            margin: 0.65rem 0 0.25rem;
        }
        .sidebar-copy {
            color: var(--muted);
            font-size: 0.88rem;
            line-height: 1.45;
            margin-bottom: 1rem;
        }
        .app-hero {
            align-items: center;
            background: linear-gradient(135deg, #ffffff 0%, #f8fbff 100%);
            border: 1px solid rgba(148, 163, 184, 0.25);
            border-radius: 16px;
            box-shadow: var(--shadow-soft);
            margin-bottom: 1.2rem;
            overflow: hidden;
            padding: 24px;
            text-align: center;
        }
        .hero-copy {
            display: flex;
            flex-direction: column;
            justify-content: center;
            min-width: 0;
            width: 100%;
        }
        .app-title {
            color: var(--ink);
            font-size: 1.7rem;
            font-weight: 800;
            letter-spacing: 0;
            line-height: 1.14;
            margin: 0;
        }
        .header-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 1.05rem;
        }
        .status-pill {
            border: 1px solid transparent;
            border-radius: 999px;
            display: inline-flex;
            font-size: 0.82rem;
            font-weight: 700;
            line-height: 1;
            padding: 9px 12px;
            white-space: nowrap;
        }
        .pill-ok {
            background: var(--success-bg);
            border-color: #b7ebcd;
            color: var(--success-text);
        }
        .pill-warn {
            background: var(--warn-bg);
            border-color: #fedf89;
            color: var(--warn-text);
        }
        .section-kicker,
        .section-eyebrow {
            color: var(--brand);
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            margin-bottom: 0.5rem;
            text-transform: uppercase;
        }
        .section-header {
            margin: 1rem 0 0.35rem;
        }
        .panel-heading {
            align-items: flex-start;
            display: flex;
            justify-content: space-between;
            gap: 20px;
            margin-bottom: 0.8rem;
        }
        .panel-title {
            color: var(--ink);
            font-size: 1.18rem;
            font-weight: 750;
            letter-spacing: 0;
            line-height: 1.3;
            margin: 0 0 0.35rem;
        }
        .panel-copy {
            color: var(--muted);
            font-size: 0.93rem;
            line-height: 1.55;
            margin: 0;
            max-width: 720px;
        }
        .upload-shell {
            align-items: center;
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 14px 14px 0 0;
            box-shadow: var(--shadow-soft);
            display: flex;
            gap: 14px;
            padding: 18px;
        }
        .upload-icon {
            align-items: center;
            background: #eff6ff;
            border: 1px solid #bfdbfe;
            border-radius: 12px;
            color: var(--brand-dark);
            display: inline-flex;
            flex: 0 0 48px;
            font-size: 0.78rem;
            font-weight: 900;
            height: 48px;
            justify-content: center;
        }
        .upload-title {
            color: var(--ink);
            font-size: 1rem;
            font-weight: 800;
            margin-bottom: 0.25rem;
        }
        .upload-copy {
            color: var(--muted);
            font-size: 0.9rem;
            line-height: 1.45;
        }
        [data-testid="stFileUploader"] {
            background: #ffffff;
            border: 1.5px dashed #94a3b8;
            border-radius: 0 0 14px 14px;
            box-shadow: var(--shadow-soft);
            margin-top: 0;
            padding: 18px;
        }
        [data-testid="stFileUploader"] section {
            padding: 8px 6px;
        }
        .side-panel {
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 14px;
            box-shadow: var(--shadow-soft);
            margin-bottom: 0.65rem;
            padding: 16px;
        }
        .side-panel-label {
            color: var(--muted);
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            margin-bottom: 0.55rem;
            text-transform: uppercase;
        }
        .step-row {
            align-items: center;
            display: flex;
            gap: 10px;
            min-height: 30px;
        }
        .step-row span {
            align-items: center;
            background: #edf2ff;
            border: 1px solid #c7d7fe;
            border-radius: 999px;
            color: var(--brand-dark);
            display: inline-flex;
            font-size: 0.78rem;
            font-weight: 800;
            height: 22px;
            justify-content: center;
            width: 22px;
        }
        .step-row p {
            color: #344054;
            font-size: 0.9rem;
            font-weight: 650;
            margin: 0;
        }
        .stButton > button,
        .stDownloadButton > button {
            border-radius: 10px !important;
            font-weight: 750 !important;
            min-height: 42px;
        }
        .stButton > button[kind="primary"] {
            background: var(--brand) !important;
            border-color: var(--brand) !important;
            color: #ffffff !important;
            box-shadow: 0 8px 18px rgba(36, 87, 197, 0.22);
        }
        .stButton > button[kind="primary"]:hover {
            background: var(--brand-dark) !important;
            border-color: var(--brand-dark) !important;
        }
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid var(--line);
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
        .section-card {
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 14px;
            box-shadow: var(--shadow-soft);
            margin-top: 0.9rem;
            padding: 18px 20px;
        }
        .section-title {
            color: var(--ink);
            font-size: 1rem;
            font-weight: 750;
            margin: 0 0 0.7rem;
        }
        .section-copy {
            color: var(--muted);
            font-size: 0.9rem;
            line-height: 1.5;
            margin-top: -0.35rem;
        }
        .table-card {
            margin-bottom: 0.8rem;
        }
        .compact-heading {
            color: var(--ink);
            font-size: 0.92rem;
            font-weight: 750;
            margin: 1rem 0 0.4rem;
        }
        .folder-status-card {
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 14px;
            box-shadow: var(--shadow-soft);
            min-height: 128px;
            padding: 16px;
        }
        .folder-count {
            color: var(--brand);
            font-size: 1.65rem;
            font-weight: 850;
            line-height: 1.1;
            margin-bottom: 0.5rem;
        }
        .folder-title {
            color: var(--ink);
            font-size: 0.95rem;
            font-weight: 800;
            margin-bottom: 0.35rem;
        }
        .folder-copy {
            color: var(--muted);
            font-size: 0.82rem;
            line-height: 1.4;
        }
        .address-text {
            color: #344054;
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
            background: var(--success-bg);
            color: var(--success-text);
        }
        .status-review {
            background: var(--warn-bg);
            color: var(--warn-text);
        }
        .status-failed {
            background: var(--danger-bg);
            color: var(--danger-text);
        }
        div[data-testid="stTabs"] [role="tablist"] {
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 12px;
            box-shadow: var(--shadow-soft);
            gap: 4px;
            margin-top: 1.1rem;
            padding: 6px;
        }
        div[data-testid="stTabs"] button[role="tab"] {
            border-radius: 9px;
            color: #667085;
            font-weight: 750;
            padding: 10px 14px;
        }
        div[data-testid="stTabs"] button[aria-selected="true"] {
            background: #eff4ff;
            color: var(--brand);
        }
        div[data-testid="stDataFrame"] {
            border: 1px solid var(--line);
            border-radius: 12px;
            box-shadow: var(--shadow-soft);
            overflow: hidden;
        }
        [data-testid="stExpander"] {
            background: #ffffff;
            border: 1px solid var(--line) !important;
            border-radius: 12px;
            box-shadow: var(--shadow-soft);
            overflow: hidden;
        }
        [data-testid="stTextInput"] input {
            border-radius: 10px;
        }
        .export-card {
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 14px;
            box-shadow: var(--shadow-soft);
            min-height: 116px;
            margin-top: 0.4rem;
            margin-bottom: 0.65rem;
            padding: 18px;
        }
        .export-title {
            color: var(--ink);
            font-size: 1rem;
            font-weight: 800;
            margin-bottom: 0.45rem;
        }
        .export-copy {
            color: var(--muted);
            font-size: 0.86rem;
            line-height: 1.45;
        }
        .refresh-spacer {
            min-height: 1.72rem;
        }
        .stAlert {
            border-radius: 12px;
        }
        @media (max-width: 760px) {
            .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }
            .app-hero {
                border-radius: 14px;
                padding: 20px;
            }
            .app-title {
                font-size: 1.45rem;
            }
            .header-pills {
                justify-content: flex-start;
            }
            .upload-shell {
                align-items: flex-start;
                flex-direction: column;
            }
            .export-card,
            .section-card,
            .side-panel {
                border-radius: 12px;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

api_status = check_api_status()
render_header(api_status)
selected_page = render_sidebar_navigation()

if selected_page == "Auto Upload":
    auto_process_summary = render_watcher_panel()
    auto_payload = auto_process_summary.get("response")
    if auto_payload:
        st.session_state["api_payload"] = auto_payload
        st.session_state["processed_file_names"] = ("incoming_pdfs",)
        st.session_state["payload_source"] = "incoming_pdfs"
        st.session_state["auto_upload_payload"] = auto_payload
        st.markdown('<div class="section-header"><h2 class="panel-title">Extracted Results</h2></div>', unsafe_allow_html=True)
        render_extraction_results(
            auto_payload,
            total_files=len(auto_payload.get("documents", [])),
            persist_exports=False,
        )
    elif st.session_state.get("auto_upload_payload"):
        st.markdown('<div class="section-header"><h2 class="panel-title">Last Auto Upload Results</h2></div>', unsafe_allow_html=True)
        render_extraction_results(
            st.session_state["auto_upload_payload"],
            total_files=len(st.session_state["auto_upload_payload"].get("documents", [])),
            persist_exports=False,
        )
    st.stop()

if selected_page == "History":
    existing_documents = st.session_state.get("api_payload", {}).get("documents", [])
    history_df = build_upload_history_df(existing_documents, st.session_state.get("payload_source"))
    render_upload_history_tab(history_df)
    st.stop()

if selected_page == "Download Info":
    render_download_info_page()
    st.stop()

uploaded_files, process_clicked = render_upload_card()

if not uploaded_files:
    st.stop()

elif not process_clicked:
    current_file_names = tuple(file.name for file in uploaded_files)
    processed_file_names = st.session_state.get("processed_file_names")
    if "api_payload" not in st.session_state or processed_file_names != current_file_names:
        st.markdown(
            """
            <div class="section-card">
                <div class="section-title">Documents Ready</div>
                <div class="address-text">Click Start Extraction to extract and validate the selected purchase orders.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.stop()
    api_payload = st.session_state["api_payload"]
else:
    progress_bar = st.progress(0)
    progress_text = st.empty()
    progress_text.write(f"Preparing {len(uploaded_files)} document(s) for processing...")
    with st.spinner("Processing purchase order documents..."):
        api_ok, api_payload = process_files_for_streamlit(uploaded_files)
    progress_bar.progress(1.0)

    if not api_ok:
        for warning in api_payload.get("debug", {}).get("warnings", [api_payload.get("message", "Processing failed.")]):
            st.error(warning)
        st.stop()

    st.session_state["api_payload"] = api_payload
    st.session_state["processed_file_names"] = tuple(file.name for file in uploaded_files)
    st.session_state["payload_source"] = "manual_upload"
    progress_text.write(f"Processed {len(uploaded_files)} document(s).")

render_extraction_results(api_payload, total_files=len(uploaded_files), persist_exports=True)
