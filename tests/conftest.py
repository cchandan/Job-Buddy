import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests always use a throwaway SQLite file and never a real Gemma key.
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
    os.environ[name] = ""
os.environ["CAREEROS_NO_DOTENV"] = "1"
