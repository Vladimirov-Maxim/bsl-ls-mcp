"""Два канала одного сценария: MCP-методы демона и командная строка (её зовёт хук).
Пути из вызова MCP — только из разрешённых корней; командная строка работает от имени
пользователя и конфайнмента не требует."""
from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest
from checks_support import PREFIX, hits, new_repo, registry, write

from bsl_ls_mcp import rules_cli
from bsl_ls_mcp.application import server
from bsl_ls_mcp.application.rules_check import InputError

LOOP_WRITE = ["Процедура П()", "\tДля Каждого Х Из Т Цикл", "\t\tХ.Записать();", "\tКонецЦикла;",
              "КонецПроцедуры"]


@pytest.fixture
def task(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, f"CommonModules/{PREFIX}Новый/Ext/Module.bsl", LOOP_WRITE)
    rules = tmp_path / "реестр.json"
    rules.write_bytes(json.dumps(registry(), ensure_ascii=False).encode("utf-8"))
    return repo, rules


def test_mcp_methods_inside_allowed_roots(task, tmp_path, monkeypatch):
    repo, rules = task
    monkeypatch.setattr(server, "_s", dataclasses.replace(server._s, allowed_roots=(tmp_path.resolve(),)))
    result = asyncio.run(server.bsl_rules_check(repo=str(repo), rules=str(rules)))
    assert [h[1] for h in hits(result, "loop.db-write")] == [3]
    methods = asyncio.run(server.bsl_task_methods(repo=str(repo)))
    assert [(m["метод"], m["статус"]) for m in methods] == [("П", "новый")]


def test_mcp_methods_reject_paths_outside_roots(task, tmp_path, monkeypatch):
    repo, rules = task
    monkeypatch.setattr(server, "_s", dataclasses.replace(server._s, allowed_roots=(repo.resolve(),)))
    with pytest.raises(InputError, match="rules: путь вне разрешённых корней"):
        asyncio.run(server.bsl_rules_check(repo=str(repo), rules=str(rules)))
    with pytest.raises(InputError, match="repo: путь вне разрешённых корней"):
        asyncio.run(server.bsl_rules_check(repo=r"\\host\share\repo"))
    with pytest.raises(InputError, match="ровно|либо repo"):
        asyncio.run(server.bsl_rules_check(repo=str(repo), baseline=str(repo), target=str(repo)))


def test_cli_json_and_exit_codes(task, capsys):
    repo, rules = task
    assert rules_cli.main(["rules-check", "--repo", str(repo), "--rules", str(rules), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)          # loop.db-write — Warn: гейт не закрыт
    assert [h[1] for h in hits(result, "loop.db-write")] == [3]

    write(repo, f"CommonModules/{PREFIX}Новый/Ext/Module.bsl",
          ["Процедура П()", "\tНачатьТранзакцию();", "КонецПроцедуры"])
    assert rules_cli.main(["rules-check", "--repo", str(repo), "--rules", str(rules)]) == 1
    out = capsys.readouterr().out
    assert "[Fail] tx.no-commit" in out and "ИТОГ: нарушений" in out

    assert rules_cli.main(["rules-check", "--repo", str(repo), "--base", "нет-такой-ревизии"]) == 2
    assert "нет ревизии" in capsys.readouterr().err


def test_cli_out_file_and_task_methods(task, tmp_path, capsys):
    repo, rules = task
    out = tmp_path / "ответ.json"
    rules_cli.main(["rules-check", "--repo", str(repo), "--rules", str(rules), "--out", str(out)])
    assert json.loads(out.read_bytes().decode("utf-8"))["находки"]
    assert rules_cli.main(["task-methods", "--repo", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["метод"] == "П"


def test_cli_runs_on_standard_library_only(task):
    """Хук конвейера зовёт `py -m bsl_ls_mcp rules-check` прямо из исходников, без установки
    пакетов: командная строка проверок не должна тянуть mcp и прочее стороннее. `-S` отключает
    site-packages — импорт стороннего пакета здесь упадёт."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo, rules = task
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = dict(os.environ, PYTHONPATH=src, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-S", "-m", "bsl_ls_mcp", "rules-check", "--repo", str(repo),
                        "--rules", str(rules), "--json"], capture_output=True, env=env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    result = json.loads(r.stdout.decode("utf-8"))
    assert [h[1] for h in hits(result, "loop.db-write")] == [3]


def test_entry_point_routes_subcommands(task, capsys):
    from bsl_ls_mcp import cli
    repo, rules = task
    with pytest.raises(SystemExit) as exit_:
        cli.main(["task-methods", "--repo", str(repo)])
    assert exit_.value.code == 0 and "новый" in capsys.readouterr().out
