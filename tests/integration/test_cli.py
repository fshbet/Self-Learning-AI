"""CLI commands (audit P1.11): the important paths, through Typer's runner. DB-backed ones need PostgreSQL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import requires_db
from typer.testing import CliRunner

from knowledge_platform import __version__
from knowledge_platform.cli import app

runner = CliRunner(env={"COLUMNS": "240"})  # rich tables truncate cells in a narrow terminal
ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str):
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, f"{args}: exit {result.exit_code}\n{result.output}"
    return result.output


def test_version_and_plugin_commands_without_a_database():
    assert __version__ in _run("version")
    out = _run("domains", "list")
    assert "powerbi" in out and "example" in out
    out = _run("domains", "check", str(ROOT / "domains" / "example"))
    assert '"id": "example"' in out and '"knowledge_type_specs"' in out
    bad = runner.invoke(app, ["domains", "check", str(ROOT / "docs")])
    assert bad.exit_code != 0


def test_export_schema_writes_the_contract(tmp_path):
    out = _run("export", "schema", "--out", str(tmp_path))
    assert "export schema version" in out
    files = sorted(p.name for p in tmp_path.iterdir())
    assert "knowledge.schema.json" in files and "vocabulary.json" in files and "ai_knowledge.schema.json" in files
    doc = json.loads((tmp_path / "knowledge.schema.json").read_text(encoding="utf-8"))
    assert doc["x-snapshot-file"] == "knowledge.jsonl"
    assert (tmp_path / "vocabulary.json").read_bytes().endswith(b"}\n")  # LF, deterministic


@requires_db
def test_database_backed_listings_and_ops():
    out = _run("export", "list", "powerbi")
    assert "powerbi" in out
    out = _run("eval", "list", "powerbi")
    assert "powerbi" in out or "no evaluation" in out.lower() or out.strip()
    out = _run("ops", "powerbi")
    assert "queue" in out and "dead-letter" in out and "schedule" in out and "items by status" in out
    metrics = json.loads(_run("ops", "powerbi", "--json"))
    kb = metrics["knowledge"]  # P2.0.1 growth metrics: every count the before/after comparison relies on
    assert {"sources", "documents", "knowledge_items", "evidence", "relationships", "conflicts"} <= set(kb)
    assert kb["knowledge_items"]["total"] == sum(kb["knowledge_items"]["by_status"].values())
    assert kb["evidence"]["verified"] <= kb["evidence"]["total"]
    assert {"needs_review", "needs_revalidation", "negative", "superseded"} <= set(kb["knowledge_items"])
    out = _run("domains", "sync", "example")
    assert "sources_created" in out
    missing = runner.invoke(app, ["export", "verify", "00000000-0000-0000-0000-000000000000"])
    assert missing.exit_code != 0


@requires_db
def test_autostart_status_reports_without_installing():
    result = runner.invoke(app, ["autostart", "status"])
    assert result.exit_code == 0
    # never installs anything: either "not installed" or an existing task's listing
    assert result.output.strip()


@pytest.mark.parametrize("cmd", [["export"], ["eval"], ["run"], ["db"], ["domains"], ["autostart"]])
def test_sub_apps_show_help_without_arguments(cmd):
    result = runner.invoke(app, cmd)
    assert "Usage" in result.output
