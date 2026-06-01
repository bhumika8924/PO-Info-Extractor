import hashlib
import html
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from utils.chunker import split_text_into_chunks
from utils.extractor import extract_fields
from utils.pdf_reader import extract_text_from_pdf
from utils.vector_store import LocalVectorStore


APP_TITLE = "PO Info Extractor"
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
CHROMA_DIR = BASE_DIR / "chroma_db"
RAG_QUERY = "PO date billing address buyer GST bill to GST"
DEFAULT_CONTEXT_COUNT = 5


UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
CHROMA_DIR.mkdir(exist_ok=True)


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


def collection_name_for_file(file_bytes: bytes) -> str:
    """Chroma collection names need to be short and URL-safe."""
    digest = hashlib.md5(file_bytes).hexdigest()[:16]
    return f"po_{digest}"


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
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="app-header">
        <h1 class="app-title">Purchase Order Intelligence Platform</h1>
        <div class="app-subtitle">Extract buyer billing information from Purchase Orders.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.container(border=True):
    st.subheader("Upload Purchase Order")
    st.write("Select a PDF purchase order to extract the buyer billing details.")
    uploaded_file = st.file_uploader("Purchase Order PDF", type=["pdf"], label_visibility="collapsed")

if uploaded_file is None:
    st.info("Upload a Purchase Order PDF to begin processing.")
    st.stop()

file_bytes = uploaded_file.getvalue()
saved_path = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_filename(uploaded_file.name)}"
saved_path.write_bytes(file_bytes)

try:
    with st.spinner("Extracting text from PDF..."):
        pdf_text = extract_text_from_pdf(saved_path)
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()

if not pdf_text.strip():
    st.error(
        "No selectable text was found in this PDF. It may be scanned or image-only. "
        "Run OCR first, then upload the text-based PDF again."
    )
    st.stop()

chunks = split_text_into_chunks(pdf_text)
if not chunks:
    st.error("Text was found, but it could not be split into searchable chunks.")
    st.stop()

try:
    with st.spinner("Analyzing purchase order..."):
        vector_store = get_vector_store()
        collection_name = collection_name_for_file(file_bytes)
        vector_store.add_chunks(collection_name, chunks, uploaded_file.name)
        retrieved_rows = vector_store.query(collection_name, RAG_QUERY, top_k=DEFAULT_CONTEXT_COUNT)
except Exception as exc:
    st.error(f"Unable to analyze this document: {exc}")
    st.stop()

retrieved_contexts = [row["text"] for row in retrieved_rows]
result = extract_fields(pdf_text, retrieved_contexts)
extraction_status = "Completed" if not result.warnings else "Needs review"

col1, col2, col3 = st.columns(3)
col1.metric("PO Date", result.po_date or "Not found")
col2.metric("Billing GST Number", result.billing_gst or "Not found")
col3.metric("Extraction Status", extraction_status)

address_display = html.escape(result.billing_address or "Billing address not found.")
st.markdown(
    f"""
    <div class="section-card">
        <div class="section-title">Billing Address</div>
        <div class="address-text">{address_display}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

if result.warnings:
    for warning in result.warnings:
        st.warning(warning)

result_row = {
    "file_name": uploaded_file.name,
    "po_date": result.po_date or "",
    "billing_address": result.billing_address or "",
    "billing_gst_number": result.billing_gst or "",
}

csv_bytes = build_csv_bytes([result_row])
output_path = OUTPUT_DIR / f"{saved_path.stem}_extracted.csv"
output_path.write_bytes(csv_bytes)

json_output_path = OUTPUT_DIR / f"{Path(safe_filename(uploaded_file.name)).stem}.json"
json_payload = {
    "file_name": uploaded_file.name,
    "saved_pdf_path": str(saved_path),
    "extracted_at": datetime.now().isoformat(timespec="seconds"),
    "po_date": result.po_date,
    "billing_address": result.billing_address,
    "billing_gst_number": result.billing_gst,
    "warnings": result.warnings,
    "debug": result.debug,
    "retrieved_context": [
        {
            "rank": idx,
            "distance": json_safe_distance(row.get("distance")),
            "metadata": row.get("metadata", {}),
            "text": row.get("text", ""),
        }
        for idx, row in enumerate(retrieved_rows, start=1)
    ],
}
json_output_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")

json_bytes = json.dumps(json_payload, indent=2).encode("utf-8")

download_col1, download_col2, _ = st.columns([1, 1, 2])
with download_col1:
    st.download_button(
        "Download JSON",
        data=json_bytes,
        file_name=f"{Path(uploaded_file.name).stem}.json",
        mime="application/json",
        use_container_width=True,
    )
with download_col2:
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name=f"{Path(uploaded_file.name).stem}_extracted.csv",
        mime="text/csv",
        use_container_width=True,
    )

st.markdown(
    '<div class="muted-note">A copy of the extraction result has been saved automatically.</div>',
    unsafe_allow_html=True,
)

with st.expander("View Source Context"):
    for idx, row in enumerate(retrieved_rows, start=1):
        st.markdown(f"**Source passage {idx}**")
        st.write(row["text"])
        if idx < len(retrieved_rows):
            st.divider()

with st.expander("Debug Details"):
    st.write("Detected buyer company:", result.debug.get("detected_buyer_company") or "Not found")
    st.write("Detected vendor company:", result.debug.get("detected_vendor_company") or "Not found")
    st.markdown("**Buyer GST source text**")
    st.text(result.debug.get("buyer_gst_source_text") or "Not found")
    st.markdown("**Vendor GST source text**")
    st.text(result.debug.get("vendor_gst_source_text") or "Not found")
