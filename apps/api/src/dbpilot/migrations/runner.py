"""Programmatic Alembic entry points used by the CLI and the test suite. The script
location is this package's own directory, so ``dbpilot migrate`` works the same from the
source tree and from the installed package in the container image."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

HERE = Path(__file__).parent


def alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def revision(database_url: str, message: str) -> None:
    command.revision(alembic_config(database_url), message=message, autogenerate=True)
