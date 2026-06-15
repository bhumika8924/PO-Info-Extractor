"""Database helpers for PO Info Extractor.

This module is responsible for:
1. Creating the MySQL database if it does not exist.
2. Creating/updating the `po_headers` and `po_items` tables.
3. Saving extracted PDF data inside one transaction.
4. Reading history rows for the Flask frontend.
"""

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd

from backend.database.config import MYSQL_CONFIG

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except ImportError:  # The app will show a clear message instead of crashing.
    mysql = None
    mysql_connector = None
    MySQLError = Exception


BASE_DIR = Path(__file__).resolve().parents[2]
SCHEMA_PATH = BASE_DIR / "backend" / "database" / "db_schema.sql"


def get_connection(database: bool = True):
    """Create a MySQL connection using the shared config.

    Set `database=False` when creating the database itself, because MySQL cannot
    connect to a database before it exists.
    """
    if mysql is None:
        raise RuntimeError("mysql-connector-python is not installed. Run: pip install -r requirements.txt")

    config = MYSQL_CONFIG.copy()
    if not database:
        config.pop("database", None)
    return mysql.connector.connect(**config)


def initialize_database() -> None:
    """Create the configured database, for example `po_extractor`, if missing."""
    connection = get_connection(database=False)
    cursor = connection.cursor()
    try:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_CONFIG['database']}`")
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def execute_schema_file(cursor) -> None:
    """Run CREATE TABLE statements from db_schema.sql.

    The file only contains schema statements, so it is safe to split on `;`.
    """
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = [statement.strip() for statement in schema_sql.split(";") if statement.strip()]
    for statement in statements:
        cursor.execute(statement)


def column_exists(cursor, table_name: str, column_name: str) -> bool:
    """Check if a column already exists before adding it.

    This keeps the app compatible with old databases that were created manually.
    """
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (MYSQL_CONFIG["database"], table_name, column_name),
    )
    return cursor.fetchone()[0] > 0


def ensure_column(cursor, table_name: str, column_name: str, definition: str) -> None:
    """Add a missing column without failing when it already exists."""
    if not column_exists(cursor, table_name, column_name):
        cursor.execute(f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {definition}")


def modify_column(cursor, table_name: str, column_name: str, definition: str) -> None:
    """Make an existing column match the current schema.

    This is useful when a table was created manually in Workbench before the app
    owned the schema. Converting item values to VARCHAR keeps the database aligned
    with the SQL schema provided for this project.
    """
    if column_exists(cursor, table_name, column_name):
        cursor.execute(f"ALTER TABLE `{table_name}` MODIFY COLUMN `{column_name}` {definition}")


def ensure_required_columns(cursor) -> None:
    """Add columns used by the app when an older table already exists.

    CREATE TABLE IF NOT EXISTS does not modify old tables, so this small
    migration step prevents manual Workbench-created tables from breaking saves.
    """
    ensure_column(cursor, "po_headers", "billing_state", "VARCHAR(100)")
    ensure_column(cursor, "po_headers", "billing_pincode", "VARCHAR(20)")
    ensure_column(cursor, "po_headers", "billing_gst_number", "VARCHAR(50)")
    ensure_column(cursor, "po_headers", "total_amount", "DECIMAL(15,2)")
    ensure_column(cursor, "po_headers", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # Extra app fields. These keep the existing frontend/history behavior intact.
    ensure_column(cursor, "po_headers", "vendor_name", "TEXT")
    ensure_column(cursor, "po_headers", "vendor_gst_number", "VARCHAR(50)")
    ensure_column(cursor, "po_headers", "extraction_status", "VARCHAR(50)")
    ensure_column(cursor, "po_headers", "warnings", "TEXT")

    ensure_column(cursor, "po_items", "item_no", "VARCHAR(50)")
    ensure_column(cursor, "po_items", "item_description", "TEXT")
    ensure_column(cursor, "po_items", "hsn_sac", "VARCHAR(100)")
    ensure_column(cursor, "po_items", "quantity", "VARCHAR(50)")
    ensure_column(cursor, "po_items", "uom", "VARCHAR(50)")
    ensure_column(cursor, "po_items", "unit_price", "VARCHAR(50)")
    ensure_column(cursor, "po_items", "tax_percent", "VARCHAR(50)")
    ensure_column(cursor, "po_items", "line_total", "VARCHAR(50)")
    ensure_column(cursor, "po_items", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # Keep old manually-created po_items tables aligned with the requested schema.
    modify_column(cursor, "po_items", "quantity", "VARCHAR(50)")
    modify_column(cursor, "po_items", "unit_price", "VARCHAR(50)")
    modify_column(cursor, "po_items", "tax_percent", "VARCHAR(50)")
    modify_column(cursor, "po_items", "line_total", "VARCHAR(50)")

    # Extra app field for line-item short names.
    ensure_column(cursor, "po_items", "item_name", "TEXT")


def create_tables_if_not_exist(connection=None) -> None:
    """Create tables and add missing columns if needed."""
    owns_connection = connection is None
    if owns_connection:
        initialize_database()
        connection = get_connection()

    cursor = connection.cursor()
    try:
        execute_schema_file(cursor)
        ensure_required_columns(cursor)
        if owns_connection:
            connection.commit()
    except Exception:
        if owns_connection:
            connection.rollback()
        raise
    finally:
        cursor.close()
        if owns_connection:
            connection.close()


def ensure_database_ready() -> dict[str, Any]:
    """Create database and tables at Flask startup.

    Returning a status dictionary lets Flask start even if MySQL is offline; the
    API can then show a helpful error instead of crashing on import.
    """
    try:
        initialize_database()
        create_tables_if_not_exist()
        return {"success": True, "connected": True, "message": "Database and tables are ready."}
    except Exception as exc:
        return {
            "success": False,
            "connected": False,
            "message": f"Database initialization failed: {type(exc).__name__}: {exc}",
        }


def normalize_decimal(value: Any) -> Decimal | None:
    """Convert extracted money-like text to Decimal for total_amount only."""
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


def text_value(row: dict[str, Any], key: str) -> str | None:
    """Return a value as text because item columns are VARCHAR in the schema."""
    value = row_value(row, key)
    if value is None:
        return None
    return str(value)


def insert_po_header(cursor, header: dict[str, Any]) -> None:
    """Insert a PO header, or update it when the same file and PO already exist."""
    file_name = text_value(header, "file_name") or ""
    po_number = text_value(header, "po_number") or ""

    cursor.execute(
        "SELECT id FROM po_headers WHERE file_name = %s AND po_number = %s",
        (file_name, po_number),
    )
    existing = cursor.fetchone()

    values = (
        text_value(header, "po_date"),
        text_value(header, "buyer_name"),
        text_value(header, "billing_address"),
        text_value(header, "billing_state"),
        text_value(header, "billing_pincode"),
        text_value(header, "billing_gst_number"),
        text_value(header, "vendor_name"),
        text_value(header, "vendor_gst_number"),
        normalize_decimal(row_value(header, "total_amount")),
        text_value(header, "extraction_status"),
        text_value(header, "warnings"),
    )

    if existing:
        cursor.execute(
            """
            UPDATE po_headers
            SET po_date = %s,
                buyer_name = %s,
                billing_address = %s,
                billing_state = %s,
                billing_pincode = %s,
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
            billing_state, billing_pincode, billing_gst_number, vendor_name,
            vendor_gst_number, total_amount, extraction_status, warnings
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (file_name, po_number, *values),
    )


def insert_po_items(cursor, file_name: str, po_number: str, item_rows: list[dict[str, Any]]) -> int:
    """Replace old item rows for this PO, then insert the latest extracted rows."""
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
                text_value(item, "file_name") or file_name,
                text_value(item, "po_number") or po_number,
                text_value(item, "item_no"),
                text_value(item, "item_name"),
                text_value(item, "item_description"),
                text_value(item, "hsn_sac"),
                text_value(item, "quantity"),
                text_value(item, "uom"),
                text_value(item, "unit_price"),
                text_value(item, "tax_percent"),
                text_value(item, "line_total"),
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
    """Save extracted headers and items to MySQL in one transaction.

    If any insert fails, rollback keeps the database from getting half-saved data.
    """
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
            file_name = text_value(header, "file_name") or ""
            po_number = text_value(header, "po_number") or ""
            insert_po_header(cursor, header)
            headers_saved += 1
            matching_items = [
                item
                for item in item_rows
                if (text_value(item, "file_name") or "") == file_name
                and (text_value(item, "po_number") or "") == po_number
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
    """Fetch latest rows from both MySQL tables for API/frontend display."""
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
