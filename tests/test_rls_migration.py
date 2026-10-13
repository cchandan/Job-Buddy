"""Migration 0012 enables RLS on every public table (Postgres only)."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

PATH = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "0012_enable_rls.py"


def _load():
    spec = importlib.util.spec_from_file_location("rls0012", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chain_and_sqlite_noop():
    module = _load()
    assert (module.revision, module.down_revision) == ("0012", "0011")
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            module.downgrade()
