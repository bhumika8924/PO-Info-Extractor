from __future__ import annotations

import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: make_json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [make_json_ready(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def write_json_file(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(make_json_ready(payload), indent=2), encoding="utf-8")
    return path


def load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def clean_document_for_output(
    document: dict[str, Any],
    source_pdf: str | Path | None = None,
    moved_to: str | None = None,
) -> dict[str, Any]:
    debug = document.get("debug") or {}
    return {
        "file_name": document.get("file_name") or (Path(source_pdf).name if source_pdf else None),
        "processed_at": timestamp(),
        "source_pdf": str(source_pdf) if source_pdf else debug.get("saved_pdf_path"),
        "moved_to": moved_to,
        "extraction_status": document.get("extraction_status") or debug.get("extraction_status"),
        "warnings": document.get("warnings") or debug.get("warnings") or [],
        "data": document.get("data") or {},
        "items": document.get("items") or [],
    }


def clean_documents_from_response(
    response: dict[str, Any],
    source_pdf: str | Path | None = None,
    moved_to: str | None = None,
) -> list[dict[str, Any]]:
    documents = response.get("documents") or []
    return [
        clean_document_for_output(document, source_pdf=source_pdf, moved_to=moved_to)
        for document in documents
    ]


def write_clean_json_outputs(
    response: dict[str, Any],
    output_dir: Path,
    source_pdf: str | Path | None = None,
    moved_to: str | None = None,
    append_history: bool = True,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_documents = clean_documents_from_response(response, source_pdf=source_pdf, moved_to=moved_to)
    written_paths = []

    for document in clean_documents:
        file_name = document.get("file_name") or "extraction.json"
        output_path = output_dir / f"{Path(file_name).stem}.json"
        written_paths.append(write_json_file(output_path, document))

    if append_history and clean_documents:
        history_path = output_dir / "processed_po_history.json"
        history = load_json_list(history_path)
        history.extend(clean_documents)
        written_paths.append(write_json_file(history_path, history))

    return written_paths


def write_csv_rows(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: make_json_ready(value) for key, value in row.items()})
    return path


def write_response_export_bundle(response: dict[str, Any], output_dir: Path) -> list[Path]:
    """Write the standard CSV/JSON download files from an extraction response."""
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = response.get("documents") or []
    data_rows = []
    header_rows = []
    item_rows = []

    for document in documents:
        file_name = document.get("file_name")
        data = document.get("data") or {}
        debug = document.get("debug") or {}
        data_rows.append({"file_name": file_name, **data})
        header_rows.append(
            {
                "file_name": file_name,
                **data,
                "extraction_status": document.get("extraction_status") or debug.get("extraction_status"),
                "warnings": "; ".join(document.get("warnings") or debug.get("warnings") or []),
            }
        )
        for item in document.get("items") or []:
            item_rows.append(
                {
                    "file_name": item.get("file_name") or file_name,
                    "po_number": item.get("po_number") or data.get("po_number"),
                    **item,
                }
            )

    written_paths = [
        write_csv_rows(output_dir / "po_data.csv", data_rows),
        write_csv_rows(output_dir / "po_headers.csv", header_rows),
        write_csv_rows(output_dir / "po_items.csv", item_rows),
        write_json_file(output_dir / "all_extractions.json", response),
    ]
    return written_paths


def remove_output_files(output_dir: Path, filenames: tuple[str, ...]) -> None:
    for filename in filenames:
        path = output_dir / filename
        if path.exists():
            path.unlink()
