from pathlib import Path
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer


DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class LocalVectorStore:
    """Small ChromaDB wrapper using local sentence-transformers embeddings."""

    def __init__(self, persist_dir: str | Path = "chroma_db", model_name: str = DEFAULT_MODEL_NAME):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.persist_dir))
        self.model = SentenceTransformer(model_name)

    def reset_collection(self, collection_name: str) -> Any:
        """Create a fresh collection for the current uploaded PDF."""
        try:
            self.client.delete_collection(collection_name)
        except Exception:
            pass

        return self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(self, collection_name: str, chunks: list[str], source_name: str) -> Any:
        """Embed chunks locally and save them in ChromaDB."""
        collection = self.reset_collection(collection_name)
        if not chunks:
            return collection

        embeddings = self.model.encode(chunks, normalize_embeddings=True).tolist()
        collection.add(
            ids=[f"{collection_name}_{idx}" for idx in range(len(chunks))],
            documents=chunks,
            embeddings=embeddings,
            metadatas=[{"source": source_name, "chunk_index": idx} for idx in range(len(chunks))],
        )
        return collection

    def query(self, collection_name: str, query_text: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Retrieve the chunks most semantically related to the query."""
        collection = self.client.get_collection(collection_name)
        query_embedding = self.model.encode([query_text], normalize_embeddings=True).tolist()
        result = collection.query(query_embeddings=query_embedding, n_results=top_k)

        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        rows: list[dict[str, Any]] = []
        for idx, document in enumerate(documents):
            rows.append(
                {
                    "text": document,
                    "metadata": metadatas[idx] if idx < len(metadatas) else {},
                    "distance": distances[idx] if idx < len(distances) else None,
                }
            )
        return rows
