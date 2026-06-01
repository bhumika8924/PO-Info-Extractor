# PO Info Extractor

A local Streamlit app that extracts these fields from Purchase Order PDFs:

- PO Date
- Billing Address / Bill To / Buyer Address
- Billing GST Number only

The app does not use OpenAI or any paid API. It extracts PDF text with `pdfplumber`, chunks the text, stores local sentence-transformer embeddings in ChromaDB, retrieves relevant context, then uses rule-based and context-aware logic to avoid returning vendor/supplier GST as billing GST.

## Folder Structure

```text
PO Info Extractor/
  app.py
  requirements.txt
  README.md
  utils/
    __init__.py
    pdf_reader.py
    chunker.py
    vector_store.py
    extractor.py
  uploads/
  outputs/
  chroma_db/
```

## Run Commands

Install Python 3.11 or 3.12 first. The easiest Windows command is:

```powershell
winget install Python.Python.3.12
```

Close PowerShell, open it again, and confirm Python 3.12 is detected:

```powershell
py -0p
```

Then open PowerShell in this folder:

```powershell
cd "D:\Bhumi\Team Computers\PO Info Extractor"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

If you installed Python 3.11 instead of 3.12, use:

```powershell
py -3.11 -m venv .venv
```

If `winget` is not available, download Python 3.12 from:

```text
https://www.python.org/downloads/release/python-31210/
```

During installation, enable **Add python.exe to PATH**.

Then open the local URL Streamlit shows, usually:

```text
http://localhost:8501
```

## Notes

- The first run may take time because `sentence-transformers/all-MiniLM-L6-v2` must download once.
- Scanned/image-only PDFs need OCR first. `pdfplumber` can only read selectable text.
- Extracted CSV files are saved in `outputs/`.
- Every extraction is also saved as JSON in `outputs/<uploaded_filename>.json`.
- Uploaded PDFs are saved in `uploads/`.
- ChromaDB data is stored in `chroma_db/`.

## Later Ollama Hook

The current app intentionally avoids LLM calls. If you later want to add Ollama, the best place is after retrieval in `app.py`: send `retrieved_contexts` to a local Ollama prompt and compare/merge its JSON response with the rule-based result from `utils/extractor.py`.
