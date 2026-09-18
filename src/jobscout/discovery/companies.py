"""Target Companies — the hand-curated list `discover` searches (DESIGN §3).

CONTEXT.md: Target Company — a company the Candidate would actually work
for, kept hand-listed in the repo.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel

DEFAULT_COMPANIES_PATH = Path("companies.yaml")


class TargetCompany(BaseModel):
    name: str
    slug: str
    careers_url: str | None = None


def load_companies(path: Path = DEFAULT_COMPANIES_PATH) -> list[TargetCompany]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return [TargetCompany(**c) for c in data.get("companies") or []]
