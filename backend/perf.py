from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import logging
import time
from typing import Any


LOGGER = logging.getLogger("scm.perf")
MAX_EVENTS = 600


def _session_state():
    try:
        import streamlit as st

        return st.session_state
    except Exception:
        return None


def _current_page() -> str:
    state = _session_state()
    if state is None:
        return ""
    return str(state.get("current_page") or state.get("selected_menu") or state.get("page") or "")


def _current_run_id() -> str:
    state = _session_state()
    if state is None:
        return ""
    return str(state.get("perf_run_id") or "")


def log_perf(message: str) -> None:
    text = f"[PERF] {message}"
    LOGGER.info(text)
    print(text, flush=True)


def record_perf_event(stage: str, elapsed_seconds: float, **fields: Any) -> None:
    row = {
        "run_id": _current_run_id(),
        "page": fields.pop("page", None) or _current_page(),
        "stage": stage,
        "elapsed_seconds": round(float(elapsed_seconds or 0.0), 4),
        **fields,
    }
    state = _session_state()
    if state is not None:
        events = list(state.get("perf_events", []))
        events.append(row)
        state["perf_events"] = events[-MAX_EVENTS:]
    details = " ".join(f"{key}={value}" for key, value in row.items() if value not in ("", None))
    log_perf(details)


@contextmanager
def perf_span(stage: str, **fields: Any):
    started_at = time.perf_counter()
    try:
        yield
    finally:
        record_perf_event(stage, time.perf_counter() - started_at, **fields)


def start_streamlit_run(page: str) -> str:
    state = _session_state()
    if state is None:
        return ""
    count = int(state.get("perf_run_count", 0) or 0) + 1
    run_id = f"RUN-{count}"
    state["perf_run_count"] = count
    state["perf_run_id"] = run_id
    state["perf_events"] = []
    state["perf_run_started_at"] = datetime.now().isoformat(timespec="seconds")
    log_perf(f"{run_id} page={page} started_at={state['perf_run_started_at']}")
    return run_id


def infer_supabase_operation(operation: str, method_name: str) -> str:
    method = method_name.lower()
    if method in {"select", "insert", "upsert", "update", "delete"}:
        return method.upper()
    if method == "rpc":
        return "RPC"
    return operation


def profile_supabase_client(client: Any) -> Any:
    return ProfiledSupabaseClient(client)


class ProfiledSupabaseClient:
    def __init__(self, wrapped: Any):
        self._wrapped = wrapped

    def table(self, table_name: str):
        return ProfiledSupabaseQuery(self._wrapped.table(table_name), table_name=table_name, operation="TABLE")

    def rpc(self, fn: str, *args: Any, **kwargs: Any):
        result = self._wrapped.rpc(fn, *args, **kwargs)
        return ProfiledSupabaseQuery(result, table_name=fn, operation="RPC")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


class ProfiledSupabaseQuery:
    def __init__(self, wrapped: Any, table_name: str, operation: str):
        self._wrapped = wrapped
        self._table_name = table_name
        self._operation = operation

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        success = True
        error = ""
        log_perf(
            "supabase_execute START "
            f"run_id={_current_run_id()} page={_current_page()} "
            f"table={self._table_name} operation={self._operation}"
        )
        try:
            return self._wrapped.execute(*args, **kwargs)
        except Exception as exc:
            success = False
            error = str(exc)
            raise
        finally:
            record_perf_event(
                "supabase_execute",
                time.perf_counter() - started_at,
                table=self._table_name,
                operation=self._operation,
                success=success,
                error=error,
            )

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._wrapped, name)
        if not callable(attr):
            return attr

        def _method(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            operation = infer_supabase_operation(self._operation, name)
            if result is self._wrapped:
                return ProfiledSupabaseQuery(result, self._table_name, operation)
            if hasattr(result, "execute"):
                return ProfiledSupabaseQuery(result, self._table_name, operation)
            return result

        return _method
