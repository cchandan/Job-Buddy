import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_plain_postgresql_url_uses_installed_driver():
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql://user:password@example.com/database"
    env["JOBBUDDY_NO_DOTENV"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", "from app import db; print(db.engine.url.drivername)"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "postgresql+psycopg2"
