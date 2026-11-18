"""Validate dataset separation and scoring without claiming GPU execution."""

import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from app.abo.dialogue_training import build_dataset, evaluate, score_prediction
from app.abo.service import bind_runtime


@pytest.fixture(scope="module")
def instruction_rows():
    bind_runtime()
    return build_dataset()


def test_instruction_split_is_reproducible_and_has_no_category_leakage(instruction_rows):
    assert instruction_rows == build_dataset()
    assert len({row["id"] for row in instruction_rows}) == len(instruction_rows)
    groups = {
        split: {row["category"] for row in instruction_rows if row["split"] == split}
        for split in ("train", "dev", "test")
    }
    assert groups["train"] and groups["dev"] and groups["test"]
    assert not groups["train"] & (groups["dev"] | groups["test"])
    assert not groups["dev"] & groups["test"]
    for row in instruction_rows:
        assert score_prediction(row, row["target"]), row["id"]
        if row["stage"] == "answer":
            evidence = row["context"]["fresh_sources"][0]
            assert "@amazon." in evidence["listing_key"]
            if row["context"]["user_context"]["needs"]["age"] is not None:
                assert evidence["age_check"] == "unknown"
                assert not row["context"]["eligible_recommendations"]


@pytest.mark.parametrize("violation", ["tool", "age", "gender", "focus", "constraint", "purpose"])
def test_planner_scoring_rejects_lost_user_requirements(instruction_rows, violation):
    candidates = [row for row in instruction_rows if row["stage"] == "plan"]
    if violation == "constraint":
        row = next(row for row in candidates if row["target"]["needs"]["constraints"])
    elif violation == "purpose":
        row = next(row for row in candidates if row["target"]["needs"]["purpose"])
    else:
        row = next(row for row in candidates if row["target"]["needs"]["age"] == 8)
    prediction = deepcopy(row["target"])
    if violation == "tool":
        prediction["tasks"] = [{"tool": "business"}]
    elif violation == "focus":
        prediction["focus"] = [2]
    elif violation == "constraint":
        prediction["needs"]["constraints"] = []
    elif violation == "purpose":
        prediction["needs"]["purpose"] = []
    elif violation == "gender":
        prediction["needs"]["gender"] = "girl"
    else:
        prediction["needs"]["age"] = 18
    assert not score_prediction(row, prediction)


def test_answer_scoring_rejects_unsupported_child_recommendations(instruction_rows):
    row = next(
        row
        for row in instruction_rows
        if row["stage"] == "answer" and row["context"]["user_context"]["needs"]["age"] == 8
    )
    prediction = deepcopy(row["target"])
    prediction["recommended"] = [row["context"]["fresh_sources"][0]["listing_key"]]
    assert not score_prediction(row, prediction)
    prediction = deepcopy(row["target"])
    prediction["citations"] = ["invented"]
    assert not score_prediction(row, prediction)
    prediction = deepcopy(row["target"])
    prediction["recommended"] = ["invented@amazon.com"]
    assert not score_prediction(row, prediction)


def test_evaluation_keeps_invalid_and_failed_generations(instruction_rows):
    rows = instruction_rows[:3]
    workspace = bind_runtime()
    with TemporaryDirectory(dir=workspace / "cache/tmp", prefix="ci-training-") as folder:
        with patch(
            "full_conversation.FullConversation.generate",
            side_effect=[json.dumps(rows[0]["target"]), "not json", RuntimeError("test failure")],
        ):
            result = evaluate(object(), rows, Path(folder), "base")
        records = [
            json.loads(line)
            for line in (Path(folder) / "base-evaluation.jsonl").read_text("utf-8").splitlines()
        ]
    assert result["cases"] == 3 and result["passed"] == 1
    assert [row["passed"] for row in records] == [True, False, False]
    assert records[2]["error"] == "RuntimeError"
