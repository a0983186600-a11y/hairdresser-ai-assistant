"""Bad imported relationships must fail before a provider can aggregate them."""

import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from assistant.adapters.mock import MockSalonDataProvider
from assistant.demo_data.generate import load_dataset
from assistant.demo_data.validate import DatasetError, load_validated_dataset, validate_dataset
from assistant.demo_data.walkthrough import walkthrough

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples/synthetic"
AS_OF = datetime.fromisoformat("2026-09-01T12:00:00+08:00")


def _write_data(path, data):
    for table, rows in data.items():
        (path / f"{table}.json").write_text(json.dumps(rows), encoding="utf-8")


@pytest.mark.parametrize("table", ["visits", "conversations", "appointments"])
def test_provider_rejects_records_with_a_different_customer_owner(monkeypatch, table):
    data = deepcopy(load_dataset())
    row = data[table][0]
    row["designer_ref"] = next(
        designer["designer_ref"] for designer in data["designers"]
        if designer["designer_ref"] != row["designer_ref"]
    )
    monkeypatch.setattr("assistant.adapters.mock.load_dataset", lambda _: data)
    with pytest.raises(ValueError, match="owner"):
        MockSalonDataProvider()


def test_duplicate_customer_ids_are_not_silently_overwritten(monkeypatch):
    data = deepcopy(load_dataset())
    data["customers"].append(deepcopy(data["customers"][0]))
    monkeypatch.setattr("assistant.adapters.mock.load_dataset", lambda _: data)
    with pytest.raises(ValueError, match="duplicate"):
        MockSalonDataProvider()


def test_shipped_and_both_example_datasets_are_valid_and_not_modified():
    for path in (SAMPLE, ROOT / "examples/empty", ROOT / "assistant/demo_data"):
        data = load_validated_dataset(path)
        original = deepcopy(data)
        counts = validate_dataset(data)
        assert counts == {key: len(rows) for key, rows in data.items()}
        assert data == original


@pytest.mark.parametrize("table,field,value", [
    ("visits", "amount_twd", -1),
    ("visits", "amount_twd", True),
    ("visits", "amount_twd", "3000"),
    ("visits", "amount_twd", 1.5),
    ("visits", "service_family", "made-up-service"),
    ("visits", "visited_at", "2026-04-01T12:00:00"),
    ("visits", "visited_at", 1234567890),
    ("visits", "customer_ref", "no-such-customer"),
    ("customers", "designer_ref", "no-such-designer"),
    ("customers", "customer_ref", ""),
    ("customers", "unexpected", "sensitive-value-must-not-appear"),
    ("conversations", "identity_ambiguity", "false"),
    ("conversations", "updated_at", "2025-01-01T12:00:00+08:00"),
    ("conversations", "safe_draft_fields", {"phone": "sensitive-value-must-not-appear"}),
])
def test_bad_rows_fail_without_echoing_values(table, field, value):
    data = load_validated_dataset(SAMPLE)
    data[table][0][field] = value
    with pytest.raises(DatasetError) as error:
        validate_dataset(data)
    assert "sensitive-value" not in str(error.value)


@pytest.mark.parametrize("table", [
    "designers", "customers", "visits", "conversations", "appointments",
])
def test_all_identifier_tables_reject_duplicates(table):
    data = load_validated_dataset(SAMPLE)
    data[table].append(deepcopy(data[table][0]))
    with pytest.raises(DatasetError, match="duplicate"):
        validate_dataset(data)


def test_files_and_top_level_shapes_must_be_complete(tmp_path):
    with pytest.raises(DatasetError, match="designers"):
        load_validated_dataset(tmp_path)
    data = load_validated_dataset(SAMPLE)
    data["visits"] = {}
    with pytest.raises(DatasetError, match="list"):
        validate_dataset(data)
    data.pop("visits")
    with pytest.raises(DatasetError, match="five"):
        validate_dataset(data)


def test_read_size_limit_and_duplicate_json_fields(tmp_path, monkeypatch):
    data = load_validated_dataset(SAMPLE)
    _write_data(tmp_path, data)
    with monkeypatch.context() as patch:
        patch.setattr("assistant.demo_data.validate.MAX_FILE_BYTES", 10)
        with pytest.raises(DatasetError):
            load_validated_dataset(tmp_path)
    (tmp_path / "visits.json").write_text('[{"amount_twd":1,"amount_twd":2}]')
    with pytest.raises(DatasetError, match="invalid JSON"):
        MockSalonDataProvider(tmp_path)


def test_walkthrough_uses_real_scoped_tools_and_never_a_model(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("walkthrough must not call a model")
    monkeypatch.setattr("assistant.agent.http_client.HttpChatClient.complete", forbidden)
    result = walkthrough(SAMPLE, "demo-a", AS_OF)
    assert result["sent"] is False
    inactive, history, draft = result["steps"]
    assert all(step["ok"] for step in result["steps"])
    assert inactive["row_count"] == 1
    assert inactive["rows"][0]["days_since_last_visit"] == 92
    assert history["result"]["known_spend_twd"] == 3000
    assert history["result"]["unknown_amount_visits"] == 1
    assert draft["result"]["service"] == "染髮"
    text = json.dumps(result, ensure_ascii=False)
    assert "示範客甲" not in text and "其他店示範客" not in text
    assert "99000" not in text and "demo-c3" not in text


def test_modified_amount_and_recency_change_the_result_not_a_recording(tmp_path):
    data = load_validated_dataset(SAMPLE)
    data["visits"][0]["amount_twd"] = 3500
    _write_data(tmp_path, data)
    result = walkthrough(tmp_path, "demo-a", AS_OF)
    assert result["steps"][1]["result"]["known_spend_twd"] == 3500
    data["visits"][1]["visited_at"] = "2026-08-31T12:00:00+08:00"
    _write_data(tmp_path, data)
    result = walkthrough(tmp_path, "demo-a", AS_OF)
    assert len(result["steps"]) == 1
    assert result["steps"][0]["rows"] == []


def test_empty_dataset_unknown_designer_and_naive_time_are_not_guessed():
    for directory, designer, stamp in (
        (ROOT / "examples/empty", "demo-a", AS_OF),
        (SAMPLE, "unknown", AS_OF),
        (SAMPLE, "demo-a", AS_OF.replace(tzinfo=None)),
    ):
        with pytest.raises(DatasetError):
            walkthrough(directory, designer, stamp)


def test_validation_cli_exit_codes_without_input_disclosure(tmp_path):
    success = subprocess.run(
        [sys.executable, "-m", "assistant.demo_data.validate", str(SAMPLE)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert success.returncode == 0
    assert json.loads(success.stdout)["rows"]["customers"] == 3
    (tmp_path / "designers.json").write_text("sensitive-value-must-not-appear")
    failed = subprocess.run(
        [sys.executable, "-m", "assistant.demo_data.validate", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert failed.returncode == 1
    assert "sensitive-value" not in failed.stdout + failed.stderr
