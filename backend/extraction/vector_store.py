from pathlib import Path
import os
import re
from typing import Any

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer


DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PERSIST_DIR = BASE_DIR / "data" / "chroma_db"


class LocalVectorStore:
    """Small ChromaDB wrapper using local sentence-transformers embeddings."""

    def __init__(self, persist_dir: str | Path | None = DEFAULT_PERSIST_DIR, model_name: str = DEFAULT_MODEL_NAME):
        self.use_chroma = True
        self.model = None
        self.collections: dict[str, dict[str, Any]] = {}
        if persist_dir is None:
            self.persist_dir = None
        else:
            self.persist_dir = Path(persist_dir)

        try:
            if persist_dir is None:
                self.client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
            else:
                self.persist_dir.mkdir(parents=True, exist_ok=True)
                self.client = chromadb.PersistentClient(
                    path=str(self.persist_dir),
                    settings=Settings(anonymized_telemetry=False),
                )
        except Exception:
            self.client = None
            self.use_chroma = False

        try:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            self.model = SentenceTransformer(model_name, local_files_only=True)
        except Exception:
            self.model = None
            self.use_chroma = False

    def collection_exists(self, collection_name: str) -> bool:
        """Check whether a collection exists before recreating it."""
        if not self.use_chroma:
            return collection_name in self.collections
        try:
            collections = self.client.list_collections()
            for collection in collections:
                name = getattr(collection, "name", collection)
                if name == collection_name:
                    return True
        except Exception:
            return False
        return False

    def reset_collection(self, collection_name: str) -> Any:
        """Create a fresh collection for the current uploaded PDF."""
        if not self.use_chroma:
            self.collections.pop(collection_name, None)
            self.collections[collection_name] = {
                "documents": [],
                "embeddings": [],
                "metadatas": [],
            }
            return self.collections[collection_name]

        if self.collection_exists(collection_name):
            self.client.delete_collection(collection_name)

        return self.client.create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(self, collection_name: str, chunks: list[str], source_name: str) -> Any:
        """Embed chunks locally and save them in ChromaDB."""
        collection = self.reset_collection(collection_name)
        if not chunks:
            return collection

        if self.model is None:
            self.collections[collection_name] = {
                "documents": chunks,
                "embeddings": [],
                "metadatas": [{"source": source_name, "chunk_index": idx} for idx in range(len(chunks))],
            }
            return self.collections[collection_name]

        embeddings = self.model.encode(chunks, normalize_embeddings=True).tolist()
        if not self.use_chroma:
            self.collections[collection_name] = {
                "documents": chunks,
                "embeddings": embeddings,
                "metadatas": [{"source": source_name, "chunk_index": idx} for idx in range(len(chunks))],
            }
            return self.collections[collection_name]

        collection.add(
            ids=[f"{collection_name}_{idx}" for idx in range(len(chunks))],
            documents=chunks,
            embeddings=embeddings,
            metadatas=[{"source": source_name, "chunk_index": idx} for idx in range(len(chunks))],
        )
        return collection

    def query(self, collection_name: str, query_text: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Retrieve the chunks most semantically related to the query."""
        if self.model is None:
            return self.keyword_query(collection_name, query_text, top_k)

        if not self.use_chroma:
            collection = self.collections[collection_name]
            query_embedding = self.model.encode([query_text], normalize_embeddings=True).tolist()[0]
            scored_rows: list[dict[str, Any]] = []
            for idx, embedding in enumerate(collection["embeddings"]):
                score = sum(query_value * doc_value for query_value, doc_value in zip(query_embedding, embedding))
                scored_rows.append(
                    {
                        "text": collection["documents"][idx],
                        "metadata": collection["metadatas"][idx],
                        "distance": 1 - score,
                    }
                )
            return sorted(scored_rows, key=lambda row: row["distance"])[:top_k]

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

    def keyword_query(self, collection_name: str, query_text: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Fallback retrieval when local embedding model files are unavailable."""
        collection = self.collections.get(collection_name, {})
        documents = collection.get("documents", [])
        metadatas = collection.get("metadatas", [])
        query_terms = set(re.findall(r"[a-z0-9]+", query_text.lower()))
        priority_terms = {
            "po",
            "date",
            "billing",
            "address",
            "buyer",
            "gst",
            "bill",
            "purchase",
            "order",
            "vendor",
            "supplier",
        }

        scored_rows: list[dict[str, Any]] = []
        for idx, document in enumerate(documents):
            terms = set(re.findall(r"[a-z0-9]+", document.lower()))
            score = len(query_terms & terms) + (2 * len(priority_terms & terms))
            scored_rows.append(
                {
                    "text": document,
                    "metadata": metadatas[idx] if idx < len(metadatas) else {},
                    "distance": 1 / (score + 1),
                }
            )

        return sorted(scored_rows, key=lambda row: row["distance"])[:top_k]
