from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from backend.utils.output_writer import write_clean_json_outputs, write_response_export_bundle
from backend.extraction.po_processor import process_uploaded_pdfs


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
INCOMING_DIR = DATA_DIR / "incoming_pdfs"
PROCESSED_DIR = DATA_DIR / "processed_pdfs"
FAILED_DIR = DATA_DIR / "failed_pdfs"
OUTPUT_DIR = DATA_DIR / "outputs"
COPY_WAIT_SECONDS = 2


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{timestamp()}] {message}", flush=True)


def ensure_runtime_folders() -> None:
    for folder in (INCOMING_DIR, PROCESSED_DIR, FAILED_DIR, OUTPUT_DIR):
        folder.mkdir(parents=True, exist_ok=True)


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


def process_pdf(pdf_path: Path) -> None:
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        return

    log(f"New PDF detected: {pdf_path.name}")
    try:
        log(f"Waiting {COPY_WAIT_SECONDS} seconds for copy to finish...")
        wait_for_copy_to_finish(pdf_path)

        log(f"Processing with existing PO extraction flow: {pdf_path.name}")
        response = process_uploaded_pdfs([pdf_path], include_debug=True, write_outputs=False)

        if extraction_failed(response):
            destination = unique_destination(FAILED_DIR, pdf_path)
            write_clean_json_outputs(response, OUTPUT_DIR, source_pdf=pdf_path, moved_to="failed_pdfs")
            write_response_export_bundle(response, OUTPUT_DIR)
            shutil.move(str(pdf_path), destination)
            log(f"Processing failed. Moved to failed_pdfs: {destination.name}")
            return

        destination = unique_destination(PROCESSED_DIR, pdf_path)
        write_clean_json_outputs(response, OUTPUT_DIR, source_pdf=pdf_path, moved_to="processed_pdfs")
        write_response_export_bundle(response, OUTPUT_DIR)
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
    log("Drop a PDF into data/incoming_pdfs to process it automatically.")

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
