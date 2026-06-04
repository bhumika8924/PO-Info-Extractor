# PO Info Extractor

PO Info Extractor is a Streamlit application backed by a Flask API for extracting structured data from Purchase Order PDF files.

The Streamlit UI uploads PDFs to `flask_api.py`. The Flask API processes each PDF with the existing utilities in `utils/` and saves extracted PO headers and line items to MySQL through `utils/database.py`.

## Architecture

```text
PDF upload
  -> Streamlit UI in app.py
  -> Flask API in flask_api.py
  -> utils/pdf_reader.py, chunker.py, vector_store.py, extractor.py
  -> extracted PO headers and line items
  -> MySQL save through utils/database.py
  -> JSON/CSV exports
```

The extraction logic remains rule-based and local. It does not require OpenAI or any paid API.

## Folder Structure

```text
PO-Info-Extractor/
  app.py
  flask_api.py
  requirements.txt
  README.md
  db_schema.sql
  .gitignore
  .streamlit/
    config.toml
  utils/
    __init__.py
    pdf_reader.py
    chunker.py
    vector_store.py
    extractor.py
    database.py
```

Generated runtime folders such as `uploads/`, `outputs/`, `chroma_db/`, and `chroma_tmp/` are ignored by Git.

## Environment Setup

Use Python 3.11 or 3.12.

```powershell
cd "D:\Bhumi\Team Computers\PO Info Extractor"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

MySQL settings are currently configured directly in `utils/database.py` for local development:

```text
host: localhost
port: 3306
user: root
password: @bhumi1234
database: po_extractor
```

## Run The App

Terminal 1:

```powershell
python flask_api.py
```

The Flask API runs at:

```text
http://127.0.0.1:5000
```

Terminal 2:

```powershell
streamlit run app.py
```

This project includes `.streamlit/config.toml`, so Streamlit runs in headless mode by default. Open:

```text
http://localhost:8501
```

## API Endpoints

```text
GET  /health
POST /extract
GET  /headers
GET  /items
```

`POST /extract` accepts one or more PDF files as multipart field `files`. The response includes:

- `headers`
- `items`
- `warnings`
- `database_save_status`
- `database_counts`
- `results`

## MySQL Setup

The app creates the configured database and tables automatically when saving extracted data. You can also create them manually:

```powershell
mysql -u root -p
```

```sql
CREATE DATABASE IF NOT EXISTS po_extractor;
USE po_extractor;
SOURCE db_schema.sql;
```

If MySQL is unavailable or credentials are wrong, extraction still returns local JSON/CSV output from the Flask API. The Streamlit UI shows the MySQL connection or save error.

## Streamlit Features

- Upload one or more PO PDFs to the Flask API.
- Review extracted PO headers, billing details, GST fields, totals, and line items.
- Save extraction results to MySQL from the API.
- View latest MySQL records through the API.
- Download extracted data as CSV and JSON.
- Clear the local vector database cache from the UI.

## Notes

- The first extraction may take time because `sentence-transformers/all-MiniLM-L6-v2` loads locally.
- Scanned or image-only PDFs need OCR before extraction. `pdfplumber` reads selectable text only.
- Uploaded PDFs are saved in `uploads/`.
- JSON and CSV outputs are saved in `outputs/`.
- Chroma and temporary vector data are ignored by Git.

## Future Scope

- OCR support for scanned PDFs
- LLM-based assistant responses
- PDF preview beside extracted fields
- User authentication
- Export templates for ERP upload
