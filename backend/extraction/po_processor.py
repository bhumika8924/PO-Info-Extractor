from __future__ import annotations

import hashlib
import traceback
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import re
from typing import Any

import pandas as pd

from backend.extraction.chunker import split_text_into_chunks
from backend.database.database import (
    ensure_database_ready,
    get_connection,
    get_database_counts,
    save_extraction_to_mysql,
)
from backend.extraction.extractor import (
    build_header_dataframe,
    build_items_dataframe,
    extract_header_fields,
    extract_item_tables,
)
from backend.utils.output_writer import write_clean_json_outputs, write_json_file
from backend.extraction.pdf_reader import extract_text_from_pdf
from backend.extraction.vector_store import LocalVectorStore


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
RAG_QUERY = "PO date billing address buyer GST bill to GST"
DEFAULT_CONTEXT_COUNT = 5

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_vector_store: LocalVectorStore | None = None


def get_vector_store() -> LocalVectorStore:
    """Use an in-memory vector store so Chroma's persistent SQLite file cannot block extraction."""
    global _vector_store
    if _vector_store is None:
        _vector_store = LocalVectorStore(persist_dir=None)
    return _vector_store


def safe_filename(filename: str) -> str:
    keep = []
    for char in filename or "upload.pdf":
        keep.append(char if char.isalnum() or char in (".", "-", "_") else "_")
    cleaned = "".join(keep).strip("_")
    return cleaned or "upload.pdf"


def collection_name_for_file(file_bytes: bytes, unique_suffix: str = "") -> str:
    digest = hashlib.md5(file_bytes).hexdigest()[:16]
    suffix = f"_{unique_suffix}" if unique_suffix else ""
    return f"po_{digest}{suffix}"[:63].strip("_")


def json_safe_distance(distance: object) -> float | None:
    if distance is None:
        return None
    try:
        return float(distance)
    except (TypeError, ValueError):
        return None


def format_retrieved_context(retrieved_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "rank": index,
            "distance": json_safe_distance(row.get("distance")),
            "metadata": row.get("metadata", {}),
            "text": row.get("text", ""),
        }
        for index, row in enumerate(retrieved_rows, start=1)
    ]


def make_json_safe(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return [make_json_safe(row) for row in value.to_dict(orient="records")]
    if isinstance(value, dict):
        return {key: make_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [make_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    try:
        is_missing = pd.isna(value)
        if isinstance(is_missing, bool) and is_missing:
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def none_if_blank(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    return value


def clean_dict_values(row: dict[str, Any]) -> dict[str, Any]:
    return {key: none_if_blank(value) for key, value in row.items()}


def ensure_warning_list(warnings: Any) -> list[str]:
    if warnings is None:
        return []
    if isinstance(warnings, list):
        return [str(warning) for warning in warnings if str(warning).strip()]
    if isinstance(warnings, str):
        return [warning for warning in warnings.split("; ") if warning.strip()]
    return [str(warnings)]


def split_billing_address(address: Any) -> tuple[Any, Any]:
    text = none_if_blank(address)
    if not isinstance(text, str):
        return None, None

    pincode_match = re.search(r"\b(\d{6})\b", text)
    pincode = pincode_match.group(1) if pincode_match else None
    states = [
        "Andhra Pradesh",
        "Arunachal Pradesh",
        "Assam",
        "Bihar",
        "Chhattisgarh",
        "Delhi",
        "Goa",
        "Gujarat",
        "Haryana",
        "Himachal Pradesh",
        "Jharkhand",
        "Karnataka",
        "Kerala",
        "Madhya Pradesh",
        "Maharashtra",
        "Manipur",
        "Meghalaya",
        "Mizoram",
        "Nagaland",
        "Odisha",
        "Punjab",
        "Rajasthan",
        "Sikkim",
        "Tamil Nadu",
        "Telangana",
        "Tripura",
        "Uttar Pradesh",
        "Uttarakhand",
        "West Bengal",
    ]
    state = next((state for state in states if re.search(rf"\b{re.escape(state)}\b", text, re.IGNORECASE)), None)
    if state is None:
        state_codes = {
            "AP": "Andhra Pradesh",
            "AR": "Arunachal Pradesh",
            "AS": "Assam",
            "BR": "Bihar",
            "CG": "Chhattisgarh",
            "DL": "Delhi",
            "GA": "Goa",
            "GJ": "Gujarat",
            "HR": "Haryana",
            "HP": "Himachal Pradesh",
            "JH": "Jharkhand",
            "KA": "Karnataka",
            "KL": "Kerala",
            "MP": "Madhya Pradesh",
            "MH": "Maharashtra",
            "MN": "Manipur",
            "ML": "Meghalaya",
            "MZ": "Mizoram",
            "NL": "Nagaland",
            "OD": "Odisha",
            "PB": "Punjab",
            "RJ": "Rajasthan",
            "SK": "Sikkim",
            "TN": "Tamil Nadu",
            "TS": "Telangana",
            "TG": "Telangana",
            "TR": "Tripura",
            "UP": "Uttar Pradesh",
            "UK": "Uttarakhand",
            "WB": "West Bengal",
        }
        state = next(
            (name for code, name in state_codes.items() if re.search(rf"(?<![A-Z]){code}(?![A-Z])", text, re.IGNORECASE)),
            None,
        )
    return state, pincode


def clean_billing_address(address: Any, state: Any, pincode: Any, gst_number: Any) -> Any:
    text = none_if_blank(address)
    if not isinstance(text, str):
        return None

    lines = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if re.search(r"\bGSTIN\b", cleaned, re.IGNORECASE):
            continue
        lines.append(cleaned)
    cleaned_text = "\n".join(lines)

    if gst_number:
        cleaned_text = re.sub(rf"\bGSTIN\s*:\s*{re.escape(str(gst_number))}\b", "", cleaned_text, flags=re.IGNORECASE)
        cleaned_text = re.sub(rf"\b{re.escape(str(gst_number))}\b", "", cleaned_text)
    if pincode:
        cleaned_text = re.sub(rf"[-,\s]*\b{re.escape(str(pincode))}\b", "", cleaned_text)
    if state:
        cleaned_text = re.sub(rf"[-,\s]*\b{re.escape(str(state))}\b", "", cleaned_text, flags=re.IGNORECASE)

    state_codes = {
        "Tamil Nadu": "TN",
        "Telangana": "TS|TG",
        "Karnataka": "KA",
        "Maharashtra": "MH",
        "Delhi": "DL",
        "Gujarat": "GJ",
        "Haryana": "HR",
        "Uttar Pradesh": "UP",
        "West Bengal": "WB",
    }
    if state in state_codes:
        cleaned_text = re.sub(rf"[-,\s]*\b({state_codes[state]})\b", "", cleaned_text, flags=re.IGNORECASE)

    cleaned_text = re.sub(r"\bIndia\b", "", cleaned_text, flags=re.IGNORECASE)
    cleaned_text = re.sub(r"\bPurchase Order\b", "", cleaned_text, flags=re.IGNORECASE)
    cleaned_text = re.sub(r"[ \t]+", " ", cleaned_text)
    cleaned_text = re.sub(r"\s+,", ",", cleaned_text)
    cleaned_text = re.sub(r",\s*,+", ",", cleaned_text)
    cleaned_text = re.sub(r"[-,\s]+$", "", cleaned_text.strip())
    return none_if_blank(cleaned_text)


def document_data_from_header(header_row: dict[str, Any]) -> dict[str, Any]:
    billing_state, billing_pincode = split_billing_address(header_row.get("billing_address"))
    billing_gst_number = none_if_blank(header_row.get("billing_gst_number"))
    billing_address = clean_billing_address(
        header_row.get("billing_address"),
        billing_state,
        billing_pincode,
        billing_gst_number,
    )
    return {
        "po_number": none_if_blank(header_row.get("po_number")),
        "po_date": none_if_blank(header_row.get("po_date")),
        "buyer_name": none_if_blank(header_row.get("buyer_name")),
        "billing_address": billing_address,
        "billing_state": billing_state,
        "billing_pincode": billing_pincode,
        "billing_gst_number": billing_gst_number,
        "vendor_name": none_if_blank(header_row.get("vendor_name")),
        "vendor_gst_number": none_if_blank(header_row.get("vendor_gst_number")),
        "total_amount": none_if_blank(header_row.get("total_amount")),
    }


def clean_items(item_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned_items = []
    for item in item_rows:
        cleaned = clean_dict_values(item)
        cleaned.pop("file_name", None)
        cleaned.pop("po_number", None)
        if cleaned.get("item_description"):
            cleaned.pop("item_name", None)
        cleaned = {key: value for key, value in cleaned.items() if value is not None}
        cleaned_items.append(cleaned)
    return cleaned_items


def read_upload(file_obj: Any, fallback_index: int) -> tuple[str, bytes]:
    filename = (
        getattr(file_obj, "filename", None)
        or getattr(file_obj, "name", None)
        or f"upload_{fallback_index}.pdf"
    )
    if isinstance(file_obj, (str, Path)):
        path = Path(file_obj)
        return path.name, path.read_bytes()
    if hasattr(file_obj, "getvalue"):
        return filename, file_obj.getvalue()
    data = file_obj.read()
    try:
        file_obj.seek(0)
    except Exception:
        pass
    return filename, data


def save_json_result(file_name: str, payload: dict[str, Any]) -> Path:
    output_path = OUTPUT_DIR / f"{Path(safe_filename(file_name)).stem}.json"
    return write_json_file(output_path, make_json_safe(payload))


def base_payload(file_name: str, saved_path: Path, logs: list[str]) -> dict[str, Any]:
    return {
        "file_name": file_name,
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


def failed_header_row(file_name: str, message: str) -> dict[str, str]:
    return {
        "file_name": file_name,
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


def process_single_pdf(file_obj: Any, run_id: str, file_index: int) -> dict[str, Any]:
    step = "starting"
    logs: list[str] = []
    file_name, file_bytes = read_upload(file_obj, file_index)
    saved_path = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{safe_filename(file_name)}"

    def log_step(current_step: str) -> None:
        logs.append(f"{datetime.now().isoformat(timespec='seconds')} | {file_name} | {current_step}")

    payload = base_payload(file_name, saved_path, logs)

    try:
        step = "validate PDF"
        log_step(step)
        if not file_name.lower().endswith(".pdf"):
            raise ValueError("Only PDF files are supported.")
        if not file_bytes:
            raise ValueError("Uploaded file is empty.")

        step = "save upload"
        log_step(step)
        saved_path.write_bytes(file_bytes)
        payload["saved_pdf_path"] = str(saved_path)

        step = "extract PDF text"
        log_step(step)
        pdf_text = extract_text_from_pdf(saved_path)
        if not pdf_text.strip():
            raise ValueError("No selectable text was found. This may be a scanned or image-only PDF.")

        step = "split text into chunks"
        log_step(step)
        chunks = split_text_into_chunks(pdf_text)
        if not chunks:
            raise ValueError("Text was found, but it could not be split into searchable sections.")

        step = "load vector store"
        log_step(step)
        vector_store = get_vector_store()

        step = "create isolated vector collection"
        log_step(step)
        collection_name = collection_name_for_file(file_bytes, f"{run_id}_{file_index}")
        vector_store.add_chunks(collection_name, chunks, file_name)

        step = "retrieve source context"
        log_step(step)
        retrieved_rows = vector_store.query(collection_name, RAG_QUERY, top_k=DEFAULT_CONTEXT_COUNT)
        retrieved_contexts = [row["text"] for row in retrieved_rows]

        step = "extract PO header fields"
        log_step(step)
        header_row = extract_header_fields(file_name, pdf_text, retrieved_contexts)

        step = "extract PO line items"
        log_step(step)
        item_rows = extract_item_tables(
            saved_path,
            pdf_text,
            file_name=file_name,
            po_number=header_row.get("po_number"),
        )

        warnings = [warning for warning in header_row.get("warnings", "").split("; ") if warning]
        if not item_rows:
            warnings.append("Line items not found.")
        status = "Completed" if not warnings else "Needs review"
        header_row["extraction_status"] = status
        header_row["warnings"] = "; ".join(warnings)

        payload.update(
            {
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
                "debug": header_row.get("debug", {}),
                "retrieved_context": format_retrieved_context(retrieved_rows),
            }
        )

        step = "save individual JSON"
        log_step(step)
        json_output_path = save_json_result(file_name, payload)

        return {
            "header_row": header_row,
            "item_rows": item_rows,
            "payload": payload,
            "json_output_path": str(json_output_path),
            "error": None,
        }
    except Exception as exc:
        message = f"{file_name} failed during {step}: {type(exc).__name__}: {exc}"
        logs.append(f"{datetime.now().isoformat(timespec='seconds')} | {message}")
        payload.update(
            {
                "extraction_status": "Failed",
                "warnings": [message],
                "error": message,
                "failed_step": step,
                "traceback": traceback.format_exc(),
            }
        )
        json_output_path = save_json_result(file_name, payload)
        return {
            "header_row": failed_header_row(file_name, message),
            "item_rows": [],
            "payload": payload,
            "json_output_path": str(json_output_path),
            "error": message,
        }


def database_status() -> dict[str, Any]:
    status = ensure_database_ready()
    return {"connected": bool(status.get("connected")), "message": status.get("message", "")}


def database_summary() -> dict[str, Any]:
    counts = get_database_counts()
    return {
        "success": bool(counts.get("success")),
        "connected": bool(counts.get("connected")),
        "total_headers_in_database": counts.get("headers_count", 0),
        "total_items_in_database": counts.get("items_count", 0),
        "message": counts.get("message", ""),
    }


def build_document_response(processed_item: dict[str, Any], include_debug: bool = False) -> dict[str, Any]:
    header_row = processed_item["header_row"]
    payload = processed_item["payload"]
    document = {
        "file_name": none_if_blank(header_row.get("file_name") or payload.get("file_name")),
        "data": document_data_from_header(header_row),
        "items": clean_items(processed_item["item_rows"]),
    }
    if include_debug:
        document["debug"] = {
            "error": payload.get("error"),
            "extraction_status": header_row.get("extraction_status"),
            "warnings": ensure_warning_list(header_row.get("warnings")),
            "failed_step": payload.get("failed_step"),
            "logs": payload.get("logs", []),
            "retrieved_context": payload.get("retrieved_context", []),
            "saved_pdf_path": payload.get("saved_pdf_path"),
            "json_output_path": processed_item.get("json_output_path"),
            "extraction_debug": payload.get("debug", {}),
        }
    return document


def process_uploaded_pdfs(
    files: list[Any],
    include_debug: bool = False,
    write_outputs: bool = True,
) -> dict[str, Any]:
    run_id = datetime.now().strftime("%H%M%S%f")
    processed = [process_single_pdf(file_obj, run_id, index) for index, file_obj in enumerate(files, start=1)]
    headers = [item["header_row"] for item in processed]
    items = [row for item in processed for row in item["item_rows"]]
    warnings = [
        warning
        for item in processed
        for warning in (item["payload"].get("warnings") or [])
        if warning
    ]

    database_save_status = save_extraction_to_mysql(headers, items)

    documents = [build_document_response(item, include_debug=include_debug) for item in processed]
    response = {
        "message": f"Processed {len(processed)} document(s) successfully.",
        "status_code": 200,
        "success": True,
        "documents": documents,
    }
    if include_debug:
        response["debug"] = {
            "warnings": warnings,
            "database_save_status": database_save_status,
            "database_summary": database_summary(),
        }
    safe_response = make_json_safe(response)

    if write_outputs:
        headers_df = build_header_dataframe(headers)
        items_df = build_items_dataframe(items)
        data_df = pd.DataFrame([document_data_from_header(header) for header in headers])
        data_df.insert(0, "file_name", [header.get("file_name", "") for header in headers])
        data_df.to_csv(OUTPUT_DIR / "po_data.csv", index=False)
        headers_df.to_csv(OUTPUT_DIR / "po_headers.csv", index=False)
        items_df.to_csv(OUTPUT_DIR / "po_items.csv", index=False)
        write_json_file(OUTPUT_DIR / "all_extractions.json", safe_response)
        write_clean_json_outputs(safe_response, OUTPUT_DIR, moved_to="manual_upload")

    return safe_response
