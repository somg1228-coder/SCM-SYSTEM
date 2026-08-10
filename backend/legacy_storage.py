from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from backend import database


_LEGACY_SCHEMA_READY = False
_TABLE_HAS_ID_COLUMN_CACHE: dict[str, bool] = {}


class CompatRow:
    def __init__(self, keys: Iterable[str], values: Iterable[Any]):
        self._keys = list(keys)
        self._values = tuple(values)
        self._mapping = dict(zip(self._keys, self._values))

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._mapping[key]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return list(self._keys)


class CompatCursor:
    def __init__(self, connection: "PostgresSqliteCompatConnection"):
        self.connection = connection
        self.description = None
        self._rows: list[CompatRow] = []
        self.lastrowid = None

    def execute(self, statement: str, parameters: Iterable[Any] | dict[str, Any] | None = None):
        self.connection._execute_into_cursor(self, statement, parameters)
        return self

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows.pop(0)

    def fetchall(self):
        rows = self._rows
        self._rows = []
        return rows

    def close(self) -> None:
        self._rows = []


class PostgresSqliteCompatConnection:
    row_factory = None

    def __init__(self):
        self._conn = database.engine.connect()
        self._transaction = self._conn.begin()
        self._last_insert_id = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self._transaction.is_active:
                if exc_type is None:
                    self._transaction.commit()
                else:
                    self._transaction.rollback()
        finally:
            self._conn.close()

    def cursor(self):
        return CompatCursor(self)

    def execute(self, statement: str, parameters: Iterable[Any] | dict[str, Any] | None = None):
        cursor = CompatCursor(self)
        return cursor.execute(statement, parameters)

    def executemany(self, statement: str, parameter_sets: Iterable[Iterable[Any] | dict[str, Any]]):
        cursor = CompatCursor(self)
        for parameters in parameter_sets:
            cursor.execute(statement, parameters)
        return cursor

    def executescript(self, script: str):
        if database.is_postgresql_url(database.DATABASE_URL):
            # DDL for legacy SQLite pages is represented in backend.models and
            # created via Base.metadata.create_all(). Keep this method harmless.
            return CompatCursor(self)
        cursor = CompatCursor(self)
        for statement in script.split(";"):
            if statement.strip():
                cursor.execute(statement)
        return cursor

    def commit(self) -> None:
        if self._transaction.is_active:
            self._transaction.commit()
        self._transaction = self._conn.begin()

    def rollback(self) -> None:
        if self._transaction.is_active:
            self._transaction.rollback()
        self._transaction = self._conn.begin()

    def close(self) -> None:
        if self._transaction.is_active:
            self._transaction.rollback()
        self._conn.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _execute_into_cursor(self, cursor: CompatCursor, statement: str, parameters: Iterable[Any] | dict[str, Any] | None) -> None:
        sql = statement.strip().rstrip(";")
        try:
            if re.match(r"^PRAGMA\s+table_info\s*\(", sql, re.IGNORECASE):
                table_name = re.search(r"\(([^)]+)\)", sql).group(1).strip().strip("\"'")
                columns = inspect(database.engine).get_columns(table_name)
                keys = ["cid", "name", "type", "notnull", "dflt_value", "pk"]
                rows = [
                    CompatRow(
                        keys,
                        [
                            index,
                            column["name"],
                            str(column.get("type", "")),
                            int(not column.get("nullable", True)),
                            column.get("default"),
                            int(column.get("primary_key", False)),
                        ],
                    )
                    for index, column in enumerate(columns)
                ]
                cursor.description = [(key, None, None, None, None, None, None) for key in keys]
                cursor._rows = rows
                return

            if re.match(r"^SELECT\s+last_insert_rowid\s*\(\s*\)", sql, re.IGNORECASE):
                cursor.description = [("last_insert_rowid()", None, None, None, None, None, None)]
                cursor._rows = [CompatRow(["last_insert_rowid()"], [self._last_insert_id])]
                return

            converted_sql, bind_params = convert_sqlite_query(sql, parameters)
            converted_sql = convert_sqlite_dialect_sql(converted_sql)
            returns_generated_id = should_return_generated_id(converted_sql)
            if returns_generated_id:
                converted_sql = f"{converted_sql} RETURNING id"

            result = self._conn.execute(text(converted_sql), bind_params)
            if result.returns_rows:
                keys = list(result.keys())
                fetched = result.fetchall()
                rows = [CompatRow(keys, row) for row in fetched]
                cursor.description = [(key, None, None, None, None, None, None) for key in keys]
                cursor._rows = rows
                if returns_generated_id and rows:
                    self._last_insert_id = rows[0][0]
                    cursor.lastrowid = self._last_insert_id
                    cursor._rows = []
            else:
                cursor.description = None
                cursor._rows = []
        except SQLAlchemyError as exc:
            database.log_database_exception("legacy sqlite-compatible query", exc)
            raise sqlite3.Error(database.sanitize_database_text(repr(exc))) from exc


def is_insert_without_returning(sql: str) -> bool:
    normalized = sql.strip().lower()
    return normalized.startswith("insert into ") and " returning " not in normalized


def inserted_table_name(sql: str) -> str | None:
    match = re.match(r'^\s*insert\s+into\s+(?:"([^"]+)"|([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?))', sql, re.IGNORECASE)
    if not match:
        return None
    table_name = match.group(1) or match.group(2) or ""
    return table_name.rsplit(".", 1)[-1].strip('"') or None


def table_has_id_column(table_name: str) -> bool:
    if table_name not in _TABLE_HAS_ID_COLUMN_CACHE:
        columns = inspect(database.engine).get_columns(table_name)
        _TABLE_HAS_ID_COLUMN_CACHE[table_name] = any(column["name"].lower() == "id" for column in columns)
    return _TABLE_HAS_ID_COLUMN_CACHE[table_name]


def should_return_generated_id(sql: str) -> bool:
    if not is_insert_without_returning(sql):
        return False
    table_name = inserted_table_name(sql)
    return bool(table_name and table_has_id_column(table_name))


def convert_sqlite_dialect_sql(statement: str) -> str:
    converted = statement
    converted = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        converted,
        flags=re.IGNORECASE,
    )
    converted = re.sub(r"\bAUTOINCREMENT\b", "", converted, flags=re.IGNORECASE)
    converted = re.sub(r"\bBLOB\b", "BYTEA", converted, flags=re.IGNORECASE)
    return converted


def convert_sqlite_query(statement: str, parameters: Iterable[Any] | dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    if parameters is None:
        return statement, {}
    if isinstance(parameters, dict):
        return statement, parameters
    values = list(parameters)
    bind_params = {f"p{index}": value for index, value in enumerate(values)}
    index = 0

    def replace_placeholder(match):
        nonlocal index
        token = f":p{index}"
        index += 1
        return token

    converted = re.sub(r"\?", replace_placeholder, statement)
    return converted, bind_params


def ensure_legacy_schema() -> None:
    global _LEGACY_SCHEMA_READY
    if _LEGACY_SCHEMA_READY or not database.is_postgresql_url(database.DATABASE_URL):
        return
    from backend import models  # noqa: F401

    database.Base.metadata.create_all(bind=database.engine)
    _LEGACY_SCHEMA_READY = True


def connect_sqlite_compatible(database_path: str | Path, *args, **kwargs):
    if database.is_sqlite_url(database.DATABASE_URL):
        return sqlite3.connect(database_path, *args, **kwargs)
    ensure_legacy_schema()
    return PostgresSqliteCompatConnection()


def legacy_store_available(database_path: str | Path) -> bool:
    if database.is_postgresql_url(database.DATABASE_URL):
        return True
    return Path(database_path).exists()


def legacy_uses_local_sqlite() -> bool:
    return database.is_sqlite_url(database.DATABASE_URL)
