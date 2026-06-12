# PO Info Extractor

PO Info Extractor extracts structured purchase order data from PDF files. It can be used from a Streamlit app, a Flask API, a static HTML/CSS/JS dashboard, or a local folder watcher.

The project is now arranged so frontend and backend code are clearly separated.

## Project Structure

```text
PO Info Extractor/
  app.py                         Streamlit launcher. Keeps `streamlit run app.py` working.
  flask_api.py                   Flask launcher. Keeps `python flask_api.py` working.
  watcher.py                     Folder watcher launcher. Keeps `python watcher.py` working.
  requirements.txt               Python dependencies.
  README.md                      Project overview and setup guide.
  .env                           Local database credentials. Ignored by Git.
  .streamlit/                    Streamlit local configuration.

  backend/
    streamlit_app.py             Main Streamlit UI.
    flask_api.py                 Flask API routes.
    watcher.py                   Folder automation worker.
    db_schema.sql                MySQL schema for PO header and item tables.
    utils/
      pdf_reader.py              Reads PDF text with pdfplumber.
      chunker.py                 Splits PDF text into chunks.
      vector_store.py            Local semantic search helper using ChromaDB.
      extractor.py               Rule-based PO field and line-item extraction.
      po_processor.py            Shared extraction pipeline used by all backends.
      database.py                MySQL connection, table creation, saves, and history reads.
      output_writer.py           JSON export and processed history writer.

  frontend/
    package.json                 Static frontend dev-server script.
    index.html                   Frontend HTML entrypoint.
    script.js                    Dashboard logic and Flask API calls.
    styles.css                   Dashboard styling.

  docs/
    Doc.docx                     Project document/reference file.

  incoming_pdfs/                 Runtime folder for auto-upload input PDFs.
  processed_pdfs/                Runtime folder for successfully processed PDFs.
  failed_pdfs/                   Runtime folder for PDFs that need review.
  uploads/                       Runtime folder for uploaded PDF copies.
  outputs/                       Runtime folder for CSV/JSON outputs and history.
  chroma_db/                     Runtime folder for local vector database data.
  chroma_tmp/                    Runtime folder for temporary vector/cache data.
```

Runtime folders are generated locally and should not be committed to Git.

## Tech Stack

### Python

Python is the main backend language. It runs the Streamlit app, Flask API, PDF extraction logic, database code, and folder watcher.

### Streamlit

Streamlit powers the main business UI in `backend/streamlit_app.py`.

It is responsible for:
- PDF upload screen
- Auto Upload from Folder status
- Extraction results tabs
- Upload History tab
- CSV/JSON download buttons

Run it with:

```powershell
streamlit run app.py
```

`app.py` is only a launcher. The real Streamlit code is in `backend/streamlit_app.py`.

### Flask

Flask powers the HTTP API in `backend/flask_api.py`.

It is responsible for:
- `/health`
- `/database-summary`
- `/extract`
- `/headers`
- `/items`

Run it with:

```powershell
python flask_api.py
```

`flask_api.py` in the root is only a launcher. The real API code is in `backend/flask_api.py`.

### Static HTML Frontend

Plain HTML, CSS, and JavaScript power the optional web frontend in `frontend/`.

It is responsible for:
- A browser dashboard outside Streamlit
- Calling the Flask API
- Showing uploaded/extracted PO data in a frontend app

Run it with:

```powershell
cd frontend
npm install
npm run dev
```

Then open:

```text
http://127.0.0.1:8080/
```

`npm run dev` checks `http://127.0.0.1:5000/health` and starts the Flask API automatically when it is not already running.

### Pandas

Pandas is used for table/data handling.

It is responsible for:
- Creating dataframes for Streamlit tables
- Formatting extracted PO header data
- Formatting extracted line-item data
- Creating CSV export bytes
- Reading MySQL query results into dataframes

### pdfplumber

pdfplumber reads selectable text and tables from PDF files.

It is responsible for:
- Opening PDF documents
- Extracting page text
- Helping detect line-item tables

Image-only scanned PDFs need OCR before this app can extract reliable text.

### ChromaDB and Sentence Transformers

ChromaDB and `sentence-transformers` provide local semantic search over extracted PDF text.

They are used to:
- Split PDF text into chunks
- Store chunks locally
- Retrieve useful context for PO field extraction

No paid API is required.

### MySQL

MySQL stores extracted PO records.

It is responsible for:
- `po_headers` table for one row per processed PO
- `po_items` table for line items
- Upload History data when database records are available

The schema is in:

```text
backend/db_schema.sql
```

### watchdog

watchdog powers the folder automation worker in `backend/watcher.py`.

It watches the local input folder and automatically processes new PDFs.

Run it with:

```powershell
python watcher.py
```

## Processing Flow

Manual Streamlit upload:

```text
app.py
  -> backend/streamlit_app.py
  -> backend/utils/po_processor.py
  -> backend/utils/pdf_reader.py
  -> backend/utils/extractor.py
  -> backend/utils/database.py
  -> outputs/
```

Flask API upload:

```text
flask_api.py
  -> backend/flask_api.py
  -> backend/utils/po_processor.py
  -> backend/utils/database.py
```

Auto folder upload:

```text
watcher.py
  -> backend/watcher.py
  -> incoming_pdfs/
  -> backend/utils/po_processor.py
  -> processed_pdfs/ or failed_pdfs/
  -> outputs/
```

Static frontend:

```text
frontend/
  -> calls Flask API at http://127.0.0.1:5000
  -> Flask API runs backend extraction
```

## Environment Setup

Use Python 3.11 or 3.12.

```powershell
cd "D:\Bhumi\Team Computers\PO Info Extractor"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

MySQL settings are loaded from environment variables or the local `.env` file:

```text
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_NAME=po_extractor
```

## Run Commands

Streamlit app:

```powershell
streamlit run app.py
```

Flask API:

```powershell
python flask_api.py
```

Folder watcher:

```powershell
python watcher.py
```

Static frontend:

```powershell
cd frontend
npm install
npm run dev
```

This starts the static frontend and starts/checks the Flask API used for PDF extraction.

## Notes

- The Streamlit app can run without the React frontend.
- The React frontend needs the Flask API running.
- The folder watcher uses the same extraction pipeline as manual upload.
- CSV/JSON exports are written to `outputs/`.
- Uploaded PDF copies are written to `uploads/`.
- The first extraction can take longer because the local sentence-transformer model may need to load.
