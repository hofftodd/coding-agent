"""SQLite databases shared by the api, orchestrator and supervisor processes.

There are two files, on purpose:

- ``foreman.db`` holds projects, tasks, runs and events. Everyone writes it.
- ``control.db`` holds stop requests and the E-stop flag. Only the API and the
  supervisor write it; the orchestrator only reads it.

The split keeps the stop path independent of the orchestrator: if the
orchestrator hangs (or is frozen) in the middle of a write transaction it holds
foreman.db's write lock, but a Stop can still be recorded in control.db and
acted on by the supervisor. Both run in WAL mode, so readers never wait for a
writer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from pathlib import Path

from sqlalchemy import Table, event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from foreman.config import get_settings
from foreman.core import models

CONTROL_TABLES: list[Table] = [
    models.Control.__table__,  # type: ignore[attr-defined]
    models.SystemFlag.__table__,  # type: ignore[attr-defined]
]


def _main_tables() -> list[Table]:
    return [t for t in SQLModel.metadata.sorted_tables if t not in CONTROL_TABLES]


@cache
def _engine_for(path: str, control: bool) -> Engine:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30}
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(engine, tables=CONTROL_TABLES if control else _main_tables())
    return engine


def get_engine() -> Engine:
    return _engine_for(str(get_settings().db_path), False)


def get_control_engine() -> Engine:
    return _engine_for(str(get_settings().control_db_path), True)


@contextmanager
def session() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as s:
        yield s


@contextmanager
def control_session() -> Iterator[Session]:
    with Session(get_control_engine(), expire_on_commit=False) as s:
        yield s


def reset_engine_cache() -> None:
    """For tests that switch FOREMAN_DATA_DIR."""
    _engine_for.cache_clear()
