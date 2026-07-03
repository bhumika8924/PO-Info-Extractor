import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"


def load_local_env() -> None:
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


load_local_env()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_DEVELOPMENT = APP_ENV in {"dev", "development", "local"}

API_KEY = os.getenv("PO_EXTRACTOR_API_KEY", os.getenv("API_KEY", "")).strip()
REQUIRE_API_KEY = env_bool("REQUIRE_API_KEY", not IS_DEVELOPMENT)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:8080,http://localhost:8080,http://127.0.0.1:5000,http://localhost:5000",
    ).split(",")
    if origin.strip()
]

MAX_UPLOAD_SIZE_BYTES = env_int("MAX_UPLOAD_SIZE_BYTES", 10 * 1024 * 1024)
MAX_PDF_PAGES = env_int("MAX_PDF_PAGES", 100)
PROCESSING_TIMEOUT_SECONDS = env_int("PROCESSING_TIMEOUT_SECONDS", 120)
ENABLE_DEBUG_RESPONSES = env_bool("ENABLE_DEBUG_RESPONSES", IS_DEVELOPMENT)
