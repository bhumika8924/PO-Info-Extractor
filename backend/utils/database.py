from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
from typing import Any

import pandas as pd

try:
    import mysql.connector
except ImportError:  # The app will show a clear message instead of crashing.
    mysql = None


BASE_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = BASE_DIR / "backend"
SCHEMA_PATH = BACKEND_DIR / "db_schema.sql"
ENV_PATH = BASE_DIR / ".env"


def load_local_env() -> None:
    """Load simple KEY=VALUE pairs from .env without overriding real environment variables."""
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


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MYSQL_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": env_int("DB_PORT", 3306),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "po_extractor"),
}


def get_connection(database: bool = True):
    """Create a MySQL connection using the project settings."""
    if mysql is None:
        raise RuntimeError("mysql-connector-python is not installed. Run: pip install -r requirements.txt")

    config = MYSQL_CONFIG.copy()
    if not database:
        config.pop("database", None)
    return mysql.connector.connect(**config)


def initialize_database() -> None:
    """Create the configured database if it does not exist."""
    connection = get_connection(database=False)
    cursor = connection.cursor()
    try:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_CONFIG['database']}`")
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def create_tables_if_not_exist(connection=None) -> None:
    """Create po_headers and po_items tables from db_schema.sql."""
    owns_connection = connection is None
    if owns_connection:
        initialize_database()
        connection = get_connection()

    cursor = connection.cursor()
    try:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        statements = [statement.strip() for statement in schema_sql.split(";") if statement.strip()]
        for statement in statements:
            cursor.execute(statement)
        if owns_connection:
            connection.commit()
    finally:
        cursor.close()
        if owns_connection:
            connection.close()


def normalize_decimal(value: Any) -> Decimal | None:
    """Convert extracted numeric text to a Decimal for MySQL."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def row_value(row: dict[str, Any], key: str) -> Any:
    """Return a safe dictionary value, converting pandas NaN to None."""
    value = row.get(key)
    if pd.isna(value):
        return None
    return value


def insert_po_header(cursor, header: dict[str, Any]) -> None:
    """Insert a new PO header or update it when file_name + po_number already exists."""
    file_name = row_value(header, "file_name") or ""
    po_number = row_value(header, "po_number") or ""

    cursor.execute(
        "SELECT id FROM po_headers WHERE file_name = %s AND po_number = %s",
        (file_name, po_number),
    )
    existing = cursor.fetchone()

    values = (
        row_value(header, "po_date"),
        row_value(header, "buyer_name"),
        row_value(header, "billing_address"),
        row_value(header, "billing_gst_number"),
        row_value(header, "vendor_name"),
        row_value(header, "vendor_gst_number"),
        normalize_decimal(row_value(header, "total_amount")),
        row_value(header, "extraction_status"),
        row_value(header, "warnings"),
    )

    if existing:
        cursor.execute(
            """
            UPDATE po_headers
            SET po_date = %s,
                buyer_name = %s,
                billing_address = %s,
                billing_gst_number = %s,
                vendor_name = %s,
                vendor_gst_number = %s,
                total_amount = %s,
                extraction_status = %s,
                warnings = %s
            WHERE file_name = %s AND po_number = %s
            """,
            (*values, file_name, po_number),
        )
        return

    cursor.execute(
        """
        INSERT INTO po_headers (
            file_name, po_number, po_date, buyer_name, billing_address,
            billing_gst_number, vendor_name, vendor_gst_number, total_amount,
            extraction_status, warnings
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (file_name, po_number, *values),
    )


def insert_po_items(cursor, file_name: str, po_number: str, item_rows: list[dict[str, Any]]) -> int:
    """Replace old items for the PO and insert the fresh extracted item rows."""
    cursor.execute(
        "DELETE FROM po_items WHERE file_name = %s AND po_number = %s",
        (file_name, po_number),
    )

    if not item_rows:
        return 0

    values = []
    for item in item_rows:
        values.append(
            (
                row_value(item, "file_name") or file_name,
                row_value(item, "po_number") or po_number,
                row_value(item, "item_no"),
                row_value(item, "item_name"),
                row_value(item, "item_description"),
                row_value(item, "hsn_sac"),
                normalize_decimal(row_value(item, "quantity")),
                row_value(item, "uom"),
                normalize_decimal(row_value(item, "unit_price")),
                normalize_decimal(row_value(item, "tax_percent")),
                normalize_decimal(row_value(item, "line_total")),
            )
        )

    cursor.executemany(
        """
        INSERT INTO po_items (
            file_name, po_number, item_no, item_name, item_description,
            hsn_sac, quantity, uom, unit_price, tax_percent, line_total
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        values,
    )
    return len(values)


def save_extraction_to_mysql(header_rows: list[dict[str, Any]], item_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Save all extracted rows to MySQL in one transaction."""
    try:
        initialize_database()
        connection = get_connection()
        cursor = connection.cursor()
    except Exception as exc:
        return {
            "success": False,
            "connected": False,
            "headers_saved": 0,
            "items_saved": 0,
            "message": (
                "MySQL connection failed. Please check MySQL service, username, "
                f"password, and database. Details: {type(exc).__name__}: {exc}"
            ),
        }

    try:
        create_tables_if_not_exist(connection)
        headers_saved = 0
        items_saved = 0

        for header in header_rows:
            file_name = row_value(header, "file_name") or ""
            po_number = row_value(header, "po_number") or ""
            insert_po_header(cursor, header)
            headers_saved += 1
            matching_items = [
                item
                for item in item_rows
                if (row_value(item, "file_name") or "") == file_name
                and (row_value(item, "po_number") or "") == po_number
            ]
            items_saved += insert_po_items(cursor, file_name, po_number, matching_items)

        connection.commit()
        return {
            "success": True,
            "connected": True,
            "headers_saved": headers_saved,
            "items_saved": items_saved,
            "message": f"Saved {headers_saved} header row(s) and {items_saved} item row(s) to MySQL.",
        }
    except Exception as exc:
        connection.rollback()
        return {
            "success": False,
            "connected": True,
            "headers_saved": 0,
            "items_saved": 0,
            "message": f"MySQL save failed. Rolled back transaction. Details: {type(exc).__name__}: {exc}",
        }
    finally:
        cursor.close()
        connection.close()


def get_database_counts() -> dict[str, Any]:
    """Return total rows currently stored in MySQL."""
    try:
        initialize_database()
        connection = get_connection()
        create_tables_if_not_exist(connection)
        cursor = connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM po_headers")
        headers_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM po_items")
        items_count = cursor.fetchone()[0]
        return {
            "success": True,
            "connected": True,
            "headers_count": headers_count,
            "items_count": items_count,
            "message": "Connected",
        }
    except Exception as exc:
        return {
            "success": False,
            "connected": False,
            "headers_count": 0,
            "items_count": 0,
            "message": (
                "MySQL connection failed. Please check MySQL service, username, "
                f"password, and database. Details: {type(exc).__name__}: {exc}"
            ),
        }
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass


def get_latest_records(limit: int = 10) -> dict[str, Any]:
    """Fetch latest rows from both MySQL tables for Streamlit display."""
    try:
        initialize_database()
        connection = get_connection()
        create_tables_if_not_exist(connection)
        headers_df = pd.read_sql(
            "SELECT * FROM po_headers ORDER BY created_at DESC, id DESC LIMIT %s",
            connection,
            params=(limit,),
        )
        items_df = pd.read_sql(
            "SELECT * FROM po_items ORDER BY created_at DESC, id DESC LIMIT %s",
            connection,
            params=(limit,),
        )
        return {
            "success": True,
            "headers": headers_df,
            "items": items_df,
            "message": "Latest records loaded.",
        }
    except Exception as exc:
        return {
            "success": False,
            "headers": pd.DataFrame(),
            "items": pd.DataFrame(),
            "message": (
                "MySQL connection failed. Please check MySQL service, username, "
                f"password, and database. Details: {type(exc).__name__}: {exc}"
            ),
        }
    finally:
        try:
            connection.close()
        except Exception:
            pass
