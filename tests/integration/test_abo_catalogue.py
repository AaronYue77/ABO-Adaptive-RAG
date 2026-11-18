"""Run the source-backed shopping acceptance suites in ordinary CPU CI."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from app.abo.service import AboDialogue, bind_runtime
from app.abo.shopping_validation import run_shopping_validation
from app.abo.validation import (
    run_category_validation,
    run_presentation_validation,
    run_validation,
)


@pytest.fixture
def dialogue():
    workspace = bind_runtime()
    import audit
    import common

    scratch = workspace / "cache/tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=scratch, prefix="ci-acceptance-") as folder:
        root = Path(folder)
        with (
            patch.object(common, "OPERATIONS", root / "operations.txt"),
            patch.object(common, "ISSUES", root / "issues.txt"),
            patch.object(audit, "ALL_RESULTS_TXT", root / "results.txt"),
        ):
            session = AboDialogue(root / "session")
            try:
                yield session, root
            finally:
                session.close()


@pytest.mark.parametrize(
    ("runner", "expected"),
    [(run_validation, 49), (run_shopping_validation, 34), (run_presentation_validation, 8)],
)
def test_shopping_followups_keep_their_source_and_scope(dialogue, runner, expected):
    session, root = dialogue
    report = root / "acceptance.json"
    result = runner(session, report)
    failures = [check for check in result["checks"] if not check["passed"]]
    assert result["cases"] == result["passed"] == expected, json.dumps(
        failures, ensure_ascii=False, indent=2
    )
    assert report.is_file()


def test_every_category_preserves_complete_source_fields(dialogue):
    session, root = dialogue
    result = run_category_validation(session, root / "categories.json")
    assert result["categories"] == result["passed"] == 576
    assert len({check["category"] for check in result["checks"]}) == 576
