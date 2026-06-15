"""MySQL configuration for the PO extractor.

Beginner note:
The app first reads values from the local `.env` file, then from real
environment variables. Environment variables win when both are present.
"""

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"
DEFAULT_DATABASE_NAME = "po_extractor"


def load_local_env() -> None:
    """Load simple KEY=VALUE lines from `.env` without overwriting existing env vars."""
    if not ENV_PATH.exists():
        return

    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#") or "=" not in cleaned:
            continue
        key, value = cleaned.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back safely on bad input."""
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


load_local_env()


MYSQL_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": env_int("DB_PORT", 3306),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", DEFAULT_DATABASE_NAME),
}
