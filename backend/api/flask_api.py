import shutil # used for moving files between folders   
from datetime import datetime # used for timestamping files
from pathlib import Path # used for file path locations

from flask import Flask, jsonify, request # used for creating the Flask app and handling requests
from flask_cors import CORS # used for enabling Cross-Origin Resource Sharing (CORS) in the Flask app

from backend.database.database import ensure_database_ready, get_latest_records 
from backend.extraction.po_processor import (
    database_status,
    database_summary,
    make_json_safe,
    process_uploaded_pdfs,
)
from backend.utils.output_writer import load_json_list, write_clean_json_outputs, write_response_export_bundle


app = Flask(__name__)
app.json.sort_keys = False
CORS(app)

# Create the database and tables when Flask starts.
# If MySQL is offline, the app still starts and `/health` reports the problem.
DATABASE_STARTUP_STATUS = ensure_database_ready()
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
INCOMING_DIR = DATA_DIR / "incoming_pdfs"
PROCESSED_DIR = DATA_DIR / "processed_pdfs"
FAILED_DIR = DATA_DIR / "failed_pdfs"
OUTPUT_DIR = DATA_DIR / "outputs"
AUTO_UPLOAD_HISTORY_PATH = BASE_DIR / "data" / "outputs" / "processed_po_history.json"


def ensure_runtime_folders() -> None:
    """Create local folders used by the Auto Upload workflow."""
    for folder in (INCOMING_DIR, PROCESSED_DIR, FAILED_DIR, OUTPUT_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def unique_destination(folder: Path, source_path: Path) -> Path:
    """Avoid overwriting an older PDF when the same name is processed again."""
    destination = folder / source_path.name
    if not destination.exists():
        return destination
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    return folder / f"{source_path.stem}_{suffix}{source_path.suffix}"


def extraction_failed(response: dict) -> bool:
    """Decide whether a processed PDF should move to processed or failed."""
    documents = response.get("documents") or []
    if not documents:
        return True
    for document in documents:
        debug = document.get("debug") or {}
        if debug.get("error") or debug.get("extraction_status") == "Failed":
            return True
    return False


def safe_incoming_pdf(file_name: str) -> Path | None:
    """Return a safe path inside data/incoming_pdfs, blocking path traversal."""
    candidate = INCOMING_DIR / Path(file_name).name
    if candidate.suffix.lower() != ".pdf" or not candidate.exists():
        return None
    return candidate


@app.get("/")
@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "api_version": "2.1.0",
            "frontend_contract_version": 2,
            "database": database_status(),
            "database_startup": DATABASE_STARTUP_STATUS,
        }
    )


@app.get("/database-summary")
def database_summary_endpoint():
    return jsonify(make_json_safe(database_summary()))


@app.get("/auto-upload-results")
def auto_upload_results():
    """Return files processed by the folder watcher for the Auto Upload page."""
    limit = request.args.get("limit", default=25, type=int)
    history = load_json_list(AUTO_UPLOAD_HISTORY_PATH)
    auto_documents = [
        document
        for document in history
        if document.get("moved_to") in {"processed_pdfs", "failed_pdfs"}
    ]
    latest_first = list(reversed(auto_documents))[:limit]
    return jsonify(
        make_json_safe(
            {
                "success": True,
                "documents": latest_first,
                "message": f"Loaded {len(latest_first)} auto-upload document(s).",
            }
        )
    )


@app.get("/auto-upload-pending")
def auto_upload_pending():
    """List new PDFs waiting in data/incoming_pdfs for the Auto Upload page."""
    ensure_runtime_folders()
    files = []
    for pdf_path in sorted(INCOMING_DIR.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True):
        stat = pdf_path.stat()
        files.append(
            {
                "file_name": pdf_path.name,
                "size_kb": round(stat.st_size / 1024, 1),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return jsonify({"success": True, "files": files, "message": f"Found {len(files)} pending PDF(s)."})


@app.post("/auto-upload-process")
def auto_upload_process():
    """Process one selected PDF from data/incoming_pdfs and return extraction results."""
    ensure_runtime_folders()
    payload = request.get_json(silent=True) or {}
    file_name = payload.get("file_name", "")
    pdf_path = safe_incoming_pdf(file_name)
    if pdf_path is None:
        return jsonify(
            {
                "success": False,
                "message": "PDF was not found in data/incoming_pdfs.",
                "documents": [],
            }
        ), 404

    response = process_uploaded_pdfs([pdf_path], include_debug=True, write_outputs=False)
    failed = extraction_failed(response)
    destination_folder = FAILED_DIR if failed else PROCESSED_DIR
    moved_to = "failed_pdfs" if failed else "processed_pdfs"
    destination = unique_destination(destination_folder, pdf_path)

    write_clean_json_outputs(response, OUTPUT_DIR, source_pdf=pdf_path, moved_to=moved_to)
    write_response_export_bundle(response, OUTPUT_DIR)
    shutil.move(str(pdf_path), destination)

    response["success"] = not failed
    response["auto_upload"] = {
        "source_folder": "data/incoming_pdfs",
        "moved_to": moved_to,
        "destination_file": destination.name,
    }
    return jsonify(make_json_safe(response))


@app.post("/extract")
def extract():
    files = request.files.getlist("files")
    if not files:
        return jsonify(
            {
                "status_code": 400,
                "success": False,
                "message": "No PDF files were uploaded. Use form-data key 'files'.",
                "documents": [],
            }
        ), 400
    include_debug = request.args.get("include_debug", "").lower() == "true"
    return jsonify(process_uploaded_pdfs(files, include_debug=include_debug))


@app.get("/headers")
def headers():
    limit = request.args.get("limit", default=25, type=int)
    latest = get_latest_records(limit=limit)
    return jsonify(
        make_json_safe(
            {
                "success": latest.get("success", False),
                "data": latest.get("headers", []),
                "message": latest.get("message", ""),
            }
        )
    )


@app.get("/items")
def items():
    limit = request.args.get("limit", default=25, type=int)
    latest = get_latest_records(limit=limit)
    return jsonify(
        make_json_safe(
            {
                "success": latest.get("success", False),
                "items": latest.get("items", []),
                "message": latest.get("message", ""),
            }
        )
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
