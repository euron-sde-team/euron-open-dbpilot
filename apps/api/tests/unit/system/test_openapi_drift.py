"""docs/api/openapi.yaml is the published wire contract (the web app and the SDKs read it):
it must match the app exactly. Regenerate with ``make openapi`` (``dbpilot openapi``)."""

from __future__ import annotations

from pathlib import Path

import yaml

from dbpilot.openapi import spec, to_yaml

DOC = Path(__file__).resolve().parents[5] / "docs" / "api" / "openapi.yaml"


def test_the_published_openapi_document_matches_the_app() -> None:
    assert DOC.exists(), "run make openapi"
    published = yaml.safe_load(DOC.read_text())
    assert published == spec(), "docs/api/openapi.yaml is stale: run make openapi"


def test_the_export_is_stable_yaml() -> None:
    assert yaml.safe_load(to_yaml(spec()))["info"]["title"] == "Euron Open DBPilot"
