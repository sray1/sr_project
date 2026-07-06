"""Shared pytest config for horse_race_predictor tests.

Sets HRP_DB_PATH to a temp file before any test module imports db.py, so every
test runs against an isolated throwaway database instead of the real
horse_tracker.db. conftest.py is imported by pytest before test modules, so the
env var is in place when db.py captures it at import time.
"""

import os
import tempfile

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
os.environ["HRP_DB_PATH"] = _TMP_DB.name