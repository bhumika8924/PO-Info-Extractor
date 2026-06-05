from __future__ import annotations

import shutil
import time
import json
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from utils.po_processor import process_uploaded_pdfs


BASE_DIR = Path(__file__).resolve().parent
INCOMING_DIR = BASE_DIR / "incoming_pdfs"
PROCESSED_DIR = BASE_DIR / "processed_pdfs"
FAILED_DIR = BASE_DIR / "failed_pdfs"
OUTPUT_DIR = BASE_DIR / "outputs"
COPY_WAIT_SECONDS = 2


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{timestamp()}] {message}", flush=True)


def ensure_runtime_folders() -> None:
    for folder in (INCOMING_DIR, PROCESSED_DIR, FAILED_DIR):
        folder.mkdir(exist_ok=True)


def unique_destination(folder: Path, source_path: Path) -> Path:
    destination = folder / source_path.name
    if not destination.exists():
        return destination

    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    return folder / f"{source_path.stem}_{suffix}{source_path.suffix}"


def wait_for_copy_to_finish(pdf_path: Path) -> None:
    time.sleep(COPY_WAIT_SECONDS)
    previous_size = -1
    for _ in range(5):
        current_size = pdf_path.stat().st_size
        if current_size == previous_size:
            return
        previous_size = current_size
        time.sleep(0.5)


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


def clean_document_for_json(document: dict, pdf_path: Path, status_folder: str) -> dict:
    debug = document.get("debug") or {}
    return {
        "file_name": document.get("file_name") or pdf_path.name,
        "processed_at": timestamp(),
        "source_pdf": str(pdf_path),
        "moved_to": status_folder,
        "extraction_status": debug.get("extraction_status"),
        "warnings": debug.get("warnings") or [],
        "data": document.get("data") or {},
        "items": document.get("items") or [],
    }


def load_json_list(json_path: Path) -> list[dict]:
    if not json_path.exists():
        return []
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def save_clean_json_outputs(response: dict, pdf_path: Path, status_folder: str) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    documents = response.get("documents") or []
    clean_documents = [
        clean_document_for_json(document, pdf_path, status_folder)
        for document in documents
    ]

    for document in clean_documents:
        output_path = OUTPUT_DIR / f"{Path(document['file_name']).stem}.json"
        output_path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    history_path = OUTPUT_DIR / "processed_po_history.json"
    history = load_json_list(history_path)
    history.extend(clean_documents)
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")


def remove_watcher_csv_outputs() -> None:
    for filename in ("po_data.csv", "po_headers.csv", "po_items.csv"):
        csv_path = OUTPUT_DIR / filename
        if csv_path.exists():
            csv_path.unlink()


def process_pdf(pdf_path: Path) -> None:
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        return

    log(f"New PDF detected: {pdf_path.name}")
    try:
        log(f"Waiting {COPY_WAIT_SECONDS} seconds for copy to finish...")
        wait_for_copy_to_finish(pdf_path)

        log(f"Processing with existing PO extraction flow: {pdf_path.name}")
        response = process_uploaded_pdfs([pdf_path], include_debug=True)

        if extraction_failed(response):
            destination = unique_destination(FAILED_DIR, pdf_path)
            save_clean_json_outputs(response, pdf_path, "failed_pdfs")
            remove_watcher_csv_outputs()
            shutil.move(str(pdf_path), destination)
            log(f"Processing failed. Moved to failed_pdfs: {destination.name}")
            return

        destination = unique_destination(PROCESSED_DIR, pdf_path)
        save_clean_json_outputs(response, pdf_path, "processed_pdfs")
        remove_watcher_csv_outputs()
        shutil.move(str(pdf_path), destination)
        log(f"Processing completed. Moved to processed_pdfs: {destination.name}")
    except Exception as exc:
        log(f"Unexpected error while processing {pdf_path.name}: {type(exc).__name__}: {exc}")
        if pdf_path.exists():
            destination = unique_destination(FAILED_DIR, pdf_path)
            shutil.move(str(pdf_path), destination)
            log(f"Moved to failed_pdfs: {destination.name}")


class IncomingPdfHandler(FileSystemEventHandler):
    def on_created(self, event) -> None:
        if not event.is_directory:
            process_pdf(Path(event.src_path))

    def on_moved(self, event) -> None:
        if not event.is_directory:
            process_pdf(Path(event.dest_path))


def main() -> None:
    ensure_runtime_folders()
    log(f"Watching folder: {INCOMING_DIR}")
    log("Drop a PDF into incoming_pdfs to process it automatically.")

    observer = Observer()
    observer.schedule(IncomingPdfHandler(), str(INCOMING_DIR), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Stopping watcher...")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
