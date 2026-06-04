import html
import json
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st
from utils.po_processor import (
    database_status,
    database_summary,
    process_uploaded_pdfs,
)


BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "outputs"
CHROMA_DIR = BASE_DIR / "chroma_db"
CHROMA_TMP_DIR = BASE_DIR / "chroma_tmp"


OUTPUT_DIR.mkdir(exist_ok=True)
CHROMA_DIR.mkdir(exist_ok=True)
CHROMA_TMP_DIR.mkdir(exist_ok=True)


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


def fetch_database_summary() -> dict:
    """Fetch database totals for the bottom system details expander."""
    return database_summary()


def render_header(status: dict) -> None:
    """Render the business header with compact health pills."""
    database = status.get("database") or {}
    system_label = "System Online" if status.get("api_connected") else "System Offline"
    database_label = "Database Connected" if database.get("connected") else "Database Unavailable"
    system_class = "pill-ok" if status.get("api_connected") else "pill-warn"
    database_class = "pill-ok" if database.get("connected") else "pill-warn"
    st.markdown(
        f"""
        <div class="app-header">
            <div>
                <h1 class="app-title">Purchase Order Intelligence Platform</h1>
                <div class="app-subtitle">Automate PO extraction, validation, and database storage.</div>
            </div>
            <div class="header-pills">
                <span class="status-pill {system_class}">{system_label}</span>
                <span class="status-pill {database_class}">{database_label}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_upload_card():
    """Render the upload workflow."""
    st.markdown(
        """
        <div class="upload-card">
            <div class="section-eyebrow">Document Intake</div>
            <h2 class="upload-title">Upload Purchase Orders</h2>
            <p class="upload-copy">Upload multiple PO PDFs to extract buyer details, GST information, and line items.</p>
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
                "File": [file.name for file in uploaded_files],
                "Size KB": [round(len(file.getvalue()) / 1024, 1) for file in uploaded_files],
            }
        )
        st.markdown('<div class="compact-heading">Selected Documents</div>', unsafe_allow_html=True)
        st.dataframe(selected_df, use_container_width=True, hide_index=True)

    action_col, reset_col, _ = st.columns([1.2, 1.2, 3])
    with action_col:
        process_clicked = st.button(
            "Process Documents",
            type="primary",
            use_container_width=True,
            disabled=not uploaded_files,
        )
    with reset_col:
        if st.button("Reset Cache", use_container_width=True):
            ok, message = reset_persistent_vector_database()
            if ok:
                st.success(message)
            else:
                st.error(message)

    return uploaded_files, process_clicked


def render_summary_cards(
    total_files: int,
    completed_count: int,
    review_count: int,
    total_line_items: int,
) -> None:
    """Render high-level batch metrics."""
    cards = [
        ("Documents Processed", total_files),
        ("Successfully Extracted", completed_count),
        ("Needs Review", review_count),
        ("Line Items Found", total_line_items),
    ]
    cols = st.columns(4)
    for col, (label, value) in zip(cols, cards):
        with col:
            st.markdown(
                f"""
                <div class="summary-card">
                    <div class="summary-label">{label}</div>
                    <div class="summary-value">{value}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


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
    st.markdown('<div class="section-title">Recent Processed Files</div>', unsafe_allow_html=True)
    st.dataframe(
        dataframe_with_status(data_df[available_columns]),
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
    st.markdown('<div class="section-title">PO Data</div>', unsafe_allow_html=True)
    st.dataframe(
        dataframe_with_status(data_df[display_columns]),
        use_container_width=True,
        hide_index=True,
    )


def render_items_tab(items_df: pd.DataFrame) -> None:
    """Render searchable line-item data without repeating document-level fields."""
    display_df = remove_empty_columns(items_df.copy())

    if display_df.empty:
        st.info("No line items were extracted.")
        return

    if "item_description" in display_df.columns:
        search = st.text_input("Search item description", key="item_search")
    else:
        search = ""
    if search and "item_description" in display_df.columns:
        display_df = display_df[
            display_df["item_description"].fillna("").str.contains(search, case=False, na=False)
        ]

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


def render_filewise_tab(documents: list[dict]) -> None:
    """Render complete data and item table for each uploaded PDF."""
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
        with st.expander(f"PDF {index}: {document.get('file_name')}", expanded=index == 1):
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
                st.dataframe(remove_empty_columns(pd.DataFrame(file_items)), use_container_width=True, hide_index=True)
            else:
                st.write("No line items found for this file.")


def render_downloads_tab(
    data_df: pd.DataFrame,
    items_df: pd.DataFrame,
    data_csv_bytes: bytes,
    items_csv_bytes: bytes,
    json_bytes: bytes,
) -> None:
    """Render export buttons and saved output paths."""
    st.markdown('<div class="section-title">Export Results</div>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            "Download PO Data CSV",
            data=data_csv_bytes,
            file_name="po_data.csv",
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
            <div class="section-title">Prepared Files</div>
            <div class="address-text">PO data, PO items, and combined extraction results are ready for download.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_system_details(api_status: dict, db_counts: dict, db_save_result: dict) -> None:
    """Keep technical diagnostics tucked away at the bottom."""
    database = api_status.get("database") or {}
    api_label = "Connected" if api_status.get("api_connected") else "Not connected"
    db_label = "Connected" if database.get("connected") or db_counts.get("connected") else "Not connected"
    total_headers = db_counts.get("total_headers_in_database", 0)
    total_items = db_counts.get("total_items_in_database", 0)
    with st.expander("System Details"):
        details_df = pd.DataFrame(
            [
                {"Field": "API connection status", "Value": api_label},
                {"Field": "Database connection status", "Value": db_label},
                {
                    "Field": "Total data records in database",
                    "Value": total_headers,
                },
                {
                    "Field": "Total item records in database",
                    "Value": total_items,
                },
            ]
        )
        st.dataframe(details_df, use_container_width=True, hide_index=True)
        if db_save_result.get("message"):
            st.caption(db_save_result["message"])
        elif database.get("message"):
            st.caption(database["message"])


st.set_page_config(page_title="Purchase Order Intelligence Platform", layout="wide")

st.markdown(
    """
    <style>
        :root {
            --ink: #182230;
            --muted: #667085;
            --line: #e4e7ec;
            --panel: #ffffff;
            --soft: #f6f8fb;
            --brand: #2457c5;
            --brand-dark: #183f91;
            --success-bg: #e7f8ef;
            --success-text: #087443;
            --warn-bg: #fff4e5;
            --warn-text: #b54708;
            --danger-bg: #fee4e2;
            --danger-text: #b42318;
        }
        .stApp {
            background:
                linear-gradient(180deg, #f7f9fc 0%, #eef3f8 100%);
            color: var(--ink);
        }
        .block-container {
            padding-top: 1.7rem;
            padding-bottom: 3rem;
            max-width: 1200px;
        }
        .app-header,
        .upload-card,
        .section-card,
        .summary-card {
            background: var(--panel);
            border: 1px solid rgba(16, 24, 40, 0.08);
            box-shadow: 0 14px 32px rgba(16, 24, 40, 0.08);
        }
        .app-header {
            align-items: center;
            border-radius: 14px;
            display: flex;
            justify-content: space-between;
            gap: 24px;
            margin-bottom: 1.25rem;
            padding: 24px 28px;
        }
        .app-title {
            color: var(--ink);
            font-size: 2rem;
            font-weight: 750;
            letter-spacing: 0;
            line-height: 1.15;
            margin: 0;
        }
        .app-subtitle {
            color: var(--muted);
            font-size: 1rem;
            margin-top: 0.45rem;
        }
        .header-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            justify-content: flex-end;
        }
        .status-pill {
            border-radius: 999px;
            display: inline-flex;
            font-size: 0.86rem;
            font-weight: 700;
            line-height: 1;
            padding: 10px 13px;
            white-space: nowrap;
        }
        .pill-ok {
            background: var(--success-bg);
            color: var(--success-text);
        }
        .pill-warn {
            background: var(--warn-bg);
            color: var(--warn-text);
        }
        .upload-card {
            border-radius: 16px;
            margin-top: 1rem;
            padding: 28px 30px 18px;
        }
        .section-eyebrow {
            color: var(--brand);
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            margin-bottom: 0.5rem;
            text-transform: uppercase;
        }
        .upload-title {
            color: var(--ink);
            font-size: 1.45rem;
            font-weight: 750;
            letter-spacing: 0;
            margin: 0 0 0.45rem;
        }
        .upload-copy {
            color: var(--muted);
            font-size: 0.98rem;
            line-height: 1.55;
            margin: 0;
            max-width: 720px;
        }
        [data-testid="stFileUploader"] {
            background: #ffffff;
            border: 1px dashed #b9c3d6;
            border-radius: 14px;
            margin-top: -0.15rem;
            padding: 18px;
        }
        [data-testid="stFileUploader"] section {
            padding: 10px 8px;
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
            border-radius: 12px;
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
            border-radius: 14px;
            padding: 20px 22px;
            margin-top: 1rem;
        }
        .section-title {
            color: var(--ink);
            font-size: 1.05rem;
            font-weight: 750;
            margin-bottom: 0.75rem;
        }
        .compact-heading {
            color: var(--ink);
            font-size: 0.92rem;
            font-weight: 750;
            margin: 1rem 0 0.4rem;
        }
        .summary-card {
            border-radius: 14px;
            min-height: 116px;
            padding: 20px 22px;
        }
        .summary-label {
            color: var(--muted);
            font-size: 0.9rem;
            font-weight: 700;
            line-height: 1.35;
        }
        .summary-value {
            color: var(--ink);
            font-size: 2rem;
            font-weight: 800;
            line-height: 1.2;
            margin-top: 0.65rem;
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
            gap: 4px;
            padding: 6px;
        }
        div[data-testid="stTabs"] button[role="tab"] {
            border-radius: 9px;
            color: #667085;
            font-weight: 750;
            padding: 10px 16px;
        }
        div[data-testid="stTabs"] button[aria-selected="true"] {
            background: #eff4ff;
            color: var(--brand);
        }
        div[data-testid="stDataFrame"] {
            border-radius: 12px;
            overflow: hidden;
        }
        .stAlert {
            border-radius: 12px;
        }
        @media (max-width: 760px) {
            .app-header {
                align-items: flex-start;
                flex-direction: column;
            }
            .header-pills {
                justify-content: flex-start;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

api_status = check_api_status()
render_header(api_status)
uploaded_files, process_clicked = render_upload_card()

if not uploaded_files:
    db_summary = fetch_database_summary() if api_status.get("api_connected") else {}
    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">Ready When You Are</div>
            <div class="address-text">Choose one or more purchase order PDFs to begin extraction.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_system_details(api_status, db_summary, {})
    st.stop()

if not process_clicked:
    current_file_names = tuple(file.name for file in uploaded_files)
    processed_file_names = st.session_state.get("processed_file_names")
    if "api_payload" not in st.session_state or processed_file_names != current_file_names:
        db_summary = fetch_database_summary() if api_status.get("api_connected") else {}
        st.markdown(
            """
            <div class="section-card">
                <div class="section-title">Documents Ready</div>
                <div class="address-text">Click Process Documents to extract and validate the selected purchase orders.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        render_system_details(api_status, db_summary, {})
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
        db_summary = fetch_database_summary() if api_status.get("api_connected") else {}
        render_system_details(api_status, db_summary, api_payload.get("debug", {}).get("database_save_status", {}))
        st.stop()

    st.session_state["api_payload"] = api_payload
    st.session_state["processed_file_names"] = tuple(file.name for file in uploaded_files)
    progress_text.write(f"Processed {len(uploaded_files)} document(s).")

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

data_csv_path = OUTPUT_DIR / "po_data.csv"
items_csv_path = OUTPUT_DIR / "po_items.csv"
all_json_path = OUTPUT_DIR / "all_extractions.json"
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
data_csv_path.write_bytes(data_csv_bytes)
items_csv_path.write_bytes(items_csv_bytes)
all_json_path.write_bytes(json_bytes)

db_save_result = api_payload.get("debug", {}).get("database_save_status", {})
db_counts = api_payload.get("debug", {}).get("database_summary", {})

completed_count = sum(
    1 for document in documents if document.get("debug", {}).get("extraction_status") == "Completed"
)
review_count = sum(
    1 for document in documents if document.get("debug", {}).get("extraction_status") in {"Needs review", "Failed"}
)
summary = {
    "total_files": len(uploaded_files),
    "completed_count": completed_count,
    "review_count": review_count,
    "total_line_items": len(item_rows),
}

st.markdown('<div class="section-title">Processing Summary</div>', unsafe_allow_html=True)
render_summary_cards(
    summary["total_files"],
    summary["completed_count"],
    summary["review_count"],
    summary["total_line_items"],
)

overview_tab, data_tab, items_tab, filewise_tab, downloads_tab = st.tabs(
    [
        "Overview",
        "PO Data",
        "Line Items",
        "File-wise Results",
        "Export",
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

with downloads_tab:
    render_downloads_tab(
        data_df,
        items_df,
        data_csv_bytes,
        items_csv_bytes,
        json_bytes,
    )

render_system_details(api_status, db_counts, db_save_result)
