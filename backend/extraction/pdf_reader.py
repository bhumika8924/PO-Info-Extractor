from pathlib import Path

import pdfplumber


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """Read text from every page of a PDF using pdfplumber."""
    pdf_path = Path(pdf_path)
    page_texts: list[str] = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    page_texts.append(f"\n--- Page {page_number} ---\n{text}")
    except Exception as exc:
        raise RuntimeError(f"Could not read PDF: {exc}") from exc

    return "\n".join(page_texts).strip()
