import re


def clean_text(text: str) -> str:
    """Normalize whitespace while keeping line breaks useful for address parsing."""
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text_into_chunks(text: str, chunk_size: int = 1800, overlap: int = 300) -> list[str]:
    """Split PDF text into overlapping chunks while preserving line breaks."""
    text = clean_text(text)
    if not text:
        return []

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break
        start = max(0, end - overlap)

    return chunks
