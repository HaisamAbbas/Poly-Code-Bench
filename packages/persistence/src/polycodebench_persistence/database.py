"""PostgreSQL engine construction without credential-bearing diagnostics."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url


class Database:
    def __init__(self, url: str, *, pool_size: int = 10, max_overflow: int = 5) -> None:
        parsed = make_url(url)
        if parsed.get_backend_name() != "postgresql":
            raise ValueError("only PostgreSQL is supported for benchmark persistence")
        if not parsed.drivername.endswith("+psycopg"):
            parsed = parsed.set(drivername="postgresql+psycopg")
        self._engine: Engine = create_engine(
            parsed,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            hide_parameters=True,
            echo=False,
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    def dispose(self) -> None:
        self._engine.dispose()
