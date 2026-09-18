import re
from pathlib import Path

import pytest

from jobscout.discovery.companies import TargetCompany, load_companies


def test_load_companies_round_trips(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text(
        "companies:\n"
        "  - name: Acme GmbH\n"
        "    slug: acme\n"
        "    careers_url: https://acme.example/careers\n"
        "  - name: Contoso\n"
        "    slug: contoso\n"
    )

    companies = load_companies(path)

    assert companies == [
        TargetCompany(name="Acme GmbH", slug="acme", careers_url="https://acme.example/careers"),
        TargetCompany(name="Contoso", slug="contoso", careers_url=None),
    ]


def test_load_companies_missing_file_returns_empty(tmp_path):
    assert load_companies(tmp_path / "nope.yaml") == []


def test_load_companies_empty_file_returns_empty(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text("companies: []\n")
    assert load_companies(path) == []


def test_load_companies_rejects_top_level_list(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text("- name: Acme GmbH\n  slug: acme\n")

    with pytest.raises(ValueError, match=re.escape(str(path))):
        load_companies(path)
