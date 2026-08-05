from __future__ import annotations

# Compatibility wrapper. The Streamlit app uses backend.database; keep this
# module as a thin re-export so legacy imports cannot create a second DB engine.
from backend.database import (  # noqa: F401
    Base,
    DATABASE_URL,
    SessionLocal,
    database_status,
    engine,
    get_db,
    init_db,
)
