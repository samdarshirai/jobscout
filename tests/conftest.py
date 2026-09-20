import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_real_network_credentials_in_tests():
    """Every LLM/discovery call a test exercises is monkeypatched at its own call
    site -- no test should ever need a real credential. `jobscout.web` module-level
    `load_dotenv()` (needed so `jobscout serve`/`uvicorn jobscout.web:app` pick up
    real `.env` values) has a confirmed-live side effect on a full `pytest` run:
    pytest imports every test module during collection, before any test runs, so
    the FIRST file that imports `jobscout.web`/`cli`/`serve` (e.g. `test_web.py`)
    loads real credentials into `os.environ` for the rest of the SESSION, not just
    its own file -- a Poll test mocking discovery got 51 real Adzuna postings back
    instead of the 1 fake one it stubbed, because `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`
    being present is discovery's only real-vs-skip gate. Strip the keys that gate a
    real network/spend call before ANY test runs, so this can't happen regardless
    of file import order."""
    for key in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"):
        os.environ.pop(key, None)
