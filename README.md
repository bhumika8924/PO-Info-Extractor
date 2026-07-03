# PO Info Extractor

PO Info Extractor extracts structured purchase order information from PDF files.
It supports manual upload from the web page, folder-based Auto Upload and a Flask API used by the HTML/CSS/JS frontend.

The project has two parts: a browser frontend (HTML/CSS/JavaScript) and a Python Flask backend. The frontend sends PDFs to the backend through HTTP API requests; the backend extracts PO data, saves database records when MySQL is available, and returns JSON.

## How the Project Works

```text
User selects a PDF
  -> JavaScript frontend (normally http://127.0.0.1:8080)
  -> POST http://127.0.0.1:5000/extract
  -> Python Flask reads and processes the PDF
  -> MySQL and data/outputs receive the result
  -> JSON returns to JavaScript
  -> The browser displays the extracted PO
```

The frontend cannot call Python functions directly. JavaScript `fetch()` sends HTTP requests to Flask routes such as `@app.post("/extract")`.

## Requirements

- Python 3.11 or 3.12
- Node.js and npm
- MySQL Server (recommended for database history)

The API can start while MySQL is offline, but database features will not work. The `/health` endpoint reports the database status.


## Project Structure

```text
PO Info Extractor/
  flask_api.py                   Local Flask launcher.
  wsgi.py                        Production WSGI entry point.
  deploy_waitress.py             Production API launcher for Windows.
  requirements.txt               Python dependencies.
  README.md                      Project overview and setup guide.
  .env                           Local database credentials. Ignored by Git.

  backend/
    watcher.py                   Folder automation worker.
    settings.py                  API security and runtime settings.
    api/
      flask_api.py               Flask application and API routes.
    database/
      config.py                  MySQL configuration.
      database.py                Database creation, saves, and history reads.
      db_schema.sql              MySQL table definitions.
    extraction/
      pdf_reader.py              Reads PDF text with pdfplumber.
      chunker.py                 Splits PDF text into chunks.
      vector_store.py            Local semantic search helper using ChromaDB.
      extractor.py               Rule-based PO field and line-item extraction.
      po_processor.py            Shared extraction pipeline used by the API and watcher.
    utils/
      output_writer.py           JSON export and processed history writer.

  frontend/
      package.json               Static frontend dev-server script.
      index.html                 Frontend HTML entrypoint.
      script.js                  Dashboard logic and Flask API calls.
      styles.css                 Dashboard styling.
      server.js                  Local frontend server.
      backendApiConfig.js        Backend address configuration.

  docs/
    Doc.docx                     Project document/reference file.

  data/                          Generated runtime data.
    incoming_pdfs/               Auto-upload input PDFs.
    processed_pdfs/              Successfully processed PDFs.
    failed_pdfs/                 PDFs that need review.
    uploads/                     Manual-upload PDF copies.
    outputs/                     CSV/JSON outputs and history.
```

Runtime folders are generated locally and should not be committed to Git.

## Tech Stack

### Python

Python is the backend language. It runs the Flask API, PDF extraction logic, database code, and folder watcher.

### Flask

Flask powers the HTTP API in `backend/api/flask_api.py`.

It is responsible for:
- `/health`
- `/database-summary`
- `/extract`
- `/headers`
- `/items`
- `/logs`
- `/auto-upload-pending`
- `/auto-upload-process`
- `/auto-upload-results`

Run it with:

```powershell
python flask_api.py
```

`flask_api.py` in the root is only a launcher. The real API code is in `backend/api/flask_api.py`.

### Static HTML Frontend

Plain HTML, CSS, and JavaScript power the web frontend in `frontend/js/`.

It is responsible for:
- PDF upload from the browser
- Calling the Flask API
- Showing uploaded and extracted PO data
- Loading database history from the API

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

Pandas is used for table and data handling.

It is responsible for:
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
- `po_processing_logs` table for extraction step logs
- Upload history data when database records are available

The schema is in:

```text
backend/database/db_schema.sql
```

### Auto Upload

The website handles Auto Upload through Flask API refresh/process buttons.
New PDFs are listed from the local input folder and processed from the browser.

## Processing Flow

Flask API upload:

```text
frontend/
  -> calls Flask API at http://127.0.0.1:5000
  -> backend/api/flask_api.py
  -> backend/extraction/po_processor.py
  -> backend/database/database.py
  -> data/outputs/
```

Auto folder upload:

```text
frontend refresh/process button
  -> Flask API auto-upload endpoints
  -> data/incoming_pdfs/
  -> backend/extraction/po_processor.py
  -> data/processed_pdfs/ or data/failed_pdfs/
  -> data/outputs/
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

Create `.env` in the project root, replace the sample password, and do not commit the file. Flask attempts to create the configured database and tables when it starts.

## Run Commands

Flask API:

```powershell
python flask_api.py
```

Static frontend:

```powershell
cd frontend/js
npm install
npm run dev
```

This starts the static frontend and starts/checks the Flask API used for PDF extraction.

The easiest complete start sequence from the project root is:

```powershell
.\.venv\Scripts\Activate.ps1
cd frontend/js
npm run dev
```

The frontend checks `/health`, starts `python flask_api.py` automatically when necessary, and prints its URL. It normally uses `http://127.0.0.1:8080/`. If port 8080 is busy, it tries the next port.

## Check That the API Works

Open this URL in a browser or send a GET request from Postman:

```text
http://127.0.0.1:5000/health
```

A working API returns JSON containing `"status": "ok"`. Its `database` field shows whether MySQL is connected.

## API Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check API and database status |
| `GET` | `/database-summary` | Get a database summary |
| `POST` | `/extract` | Upload and extract PDFs |
| `GET` | `/headers?limit=25` | Get recent PO headers |
| `GET` | `/items?limit=25` | Get recent PO items |
| `GET` | `/logs?limit=25` | Get processing logs |
| `GET` | `/auto-upload-pending` | List PDFs in `data/incoming_pdfs` |
| `POST` | `/auto-upload-process` | Process one pending PDF |
| `GET` | `/auto-upload-results?limit=25` | Get Auto Upload results |

Every local endpoint starts with `http://127.0.0.1:5000`.

## Test PDF Extraction in Postman

1. Start the Flask API.
2. Create a `POST` request to `http://127.0.0.1:5000/extract`.
3. Choose **Body**, then **form-data**.
4. Enter `files` in the **Key** column.
5. Change its type from **Text** to **File**.
6. Select a PDF and click **Send**.

The key must be exactly `files` because Flask uses `request.files.getlist("files")`. Do not manually set `Content-Type`; Postman creates it.

## Frontend API Configuration

`frontend/js/server.js` supplies the backend address. `script.js` reads it and falls back to the local Flask server:

```js
const API_BASE = window.PO_EXTRACTOR_CONFIG?.API_BASE || "http://127.0.0.1:5000";
```

To use another backend:

```powershell
$env:BACKEND_URL="http://server-address:5000"
npm run dev
```

## Auto Upload and Folder Watcher

Place PDFs in `data/incoming_pdfs/`. Successful files move to `data/processed_pdfs/`; unsuccessful files move to `data/failed_pdfs/`.

To monitor that folder continuously without the browser:

```powershell
python -m backend.watcher
```

## Development and Production

`python flask_api.py` uses Flask's local development server. For production, use:

- Windows: `python deploy_waitress.py`
- Linux: `gunicorn "wsgi:app" --bind 127.0.0.1:5000 --workers 2 --threads 4 --timeout 180`

`wsgi.py` does not create another backend. It exposes the existing Flask app to a production server. See `docs/DEPLOYMENT.md`.

## Common Problems

### No PDF files were uploaded

Use form-data key `files` and set its type to **File**.

### Frontend cannot connect

- Check that `http://127.0.0.1:5000/health` opens.
- Check the Python terminal for errors.
- Confirm that `API_BASE` uses the Flask host and port.

### MySQL connection fails

- Confirm MySQL Server is running.
- Check `.env`.
- Read the `database` field returned by `/health`.

## Notes

- The frontend needs the Flask API running.
- The folder watcher uses the same extraction pipeline as browser upload.
- CSV/JSON exports are written to `data/outputs/`.
- Uploaded PDF copies are written to `data/uploads/`.
- The first extraction can take longer because the local sentence-transformer model may need to load.
- Image-only scanned PDFs require OCR before reliable text extraction.
