import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from scripts import fetch_sample_stores
from scripts.reindex_menu_snapshots import main, rebuild_index


def snapshot(store_id="1"):
    return {
        "brand_name": "測試品牌",
        "store_name": "測試分店",
        "store_id": store_id,
        "raw_menu": {
            "status": 200,
            "menu": {"product_list_json": {"data": {"tea": {"name": "茶"}}}},
        },
    }


def write_snapshot(directory, filename="sample.json", record=None):
    path = directory / filename
    path.write_text(json.dumps(snapshot() if record is None else record))
    return path


def test_rebuild_covers_all_existing_snapshots_and_ignores_old_index(tmp_path):
    first = write_snapshot(tmp_path, "a.json")
    second = write_snapshot(tmp_path, "b.json", snapshot("2"))
    before = {path.name: path.read_bytes() for path in (first, second)}
    (tmp_path / "index.json").write_text("broken old index")
    (tmp_path / "notes.txt").write_text("not a snapshot")
    entries = rebuild_index(tmp_path)
    assert [entry["file"] for entry in entries] == ["a.json", "b.json"]
    assert [entry["product_count"] for entry in entries] == [1, 1]
    assert json.loads((tmp_path / "index.json").read_text()) == entries
    assert {path.name: path.read_bytes() for path in (first, second)} == before
    assert not list(tmp_path.glob(".index-*.tmp"))


@pytest.mark.parametrize(
    "payload",
    ["{bad", "[]", "null", "{}", '{"store_id": "1", "raw_menu": {}}'],
)
def test_bad_snapshot_keeps_previous_index(tmp_path, payload, capsys):
    write_snapshot(tmp_path)
    old_index = b"[]\n"
    (tmp_path / "index.json").write_bytes(old_index)
    (tmp_path / "broken.json").write_text(payload)
    assert main([str(tmp_path)]) == 1
    assert "broken.json" in capsys.readouterr().err
    assert (tmp_path / "index.json").read_bytes() == old_index
    assert not list(tmp_path.glob(".index-*.tmp"))


@pytest.mark.parametrize("invalid_part", ["status", "products", "store_id"])
def test_rejects_failed_or_incomplete_snapshot(tmp_path, invalid_part):
    record = snapshot()
    if invalid_part == "status":
        record["raw_menu"]["status"] = 500
    elif invalid_part == "products":
        record["raw_menu"]["menu"]["product_list_json"]["data"] = "invalid"
    else:
        record["store_id"] = ""
    write_snapshot(tmp_path, record=record)
    with pytest.raises(ValueError, match="Invalid snapshot"):
        rebuild_index(tmp_path)
    assert not (tmp_path / "index.json").exists()


def test_atomic_replace_and_failure_preserve_previous_index(tmp_path):
    write_snapshot(tmp_path)
    index = tmp_path / "index.json"
    index.write_text("previous index")

    def failed_replace(source, destination):
        assert source.parent == tmp_path
        assert destination == index
        assert index.read_text() == "previous index"
        assert len(json.loads(source.read_text())) == 1
        raise OSError("simulated replace failure")

    with patch("scripts.reindex_menu_snapshots.os.replace", failed_replace):
        with pytest.raises(OSError, match="simulated"):
            rebuild_index(tmp_path)
    assert index.read_text() == "previous index"
    assert not list(tmp_path.glob(".index-*.tmp"))


def test_empty_and_missing_directories(tmp_path):
    assert rebuild_index(tmp_path) == []
    assert main([str(tmp_path / "missing")]) == 1
    assert not (tmp_path / "missing").exists()


def test_cli_module_works_with_explicit_directory(tmp_path):
    write_snapshot(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.reindex_menu_snapshots", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Snapshots: 1; index entries: 1" in result.stdout


def test_fetch_import_has_no_directory_side_effect(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": f"{root}:{os.getenv('PYTHONPATH', '')}"}
    result = subprocess.run(
        [sys.executable, "-c", "import scripts.fetch_sample_stores"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "data").exists()


@pytest.mark.asyncio
async def test_fetch_indexes_old_and_new_snapshots(tmp_path, monkeypatch):
    old = write_snapshot(tmp_path, "old.json")
    original = old.read_bytes()
    monkeypatch.setattr(fetch_sample_stores, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(fetch_sample_stores, "TARGET_BRANDS", ["新品牌"])

    async def get(url, **kwargs):
        if url.endswith("/brands"):
            body = {"brands": [{"id": "new", "name": "新品牌"}]}
        elif url.endswith("/stores"):
            body = {"stores": [{"id": "2", "name": "新分店"}]}
        elif url.endswith("/onShelfMenu"):
            body = snapshot("2")["raw_menu"]
        else:
            body = {}
        return SimpleNamespace(status_code=200, json=lambda: body)

    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.side_effect = get
    with patch.object(fetch_sample_stores.httpx, "AsyncClient", return_value=client):
        await fetch_sample_stores.main()
    entries = json.loads((tmp_path / "index.json").read_text())
    assert {entry["store_id"] for entry in entries} == {"1", "2"}
    assert old.read_bytes() == original
