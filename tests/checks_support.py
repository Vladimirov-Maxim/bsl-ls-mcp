"""Общее для тестов проверок кода задачи: временный git-репозиторий выгрузки и
синтетический реестр правил.

Всё синтетическое: префикс доработок здесь «мой_», а не проектный, — так тесты
заодно доказывают, что префикс в инструменте не зашит. Реальный реестр проекта и
копия бенчмарка подключаются только локально, через переменные окружения
(см. test_checks_registry_live.py)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bsl_ls_mcp.domain.checks.code_rules import CATALOG  # noqa: E402

PREFIX = "мой_"


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "core.autocrlf=false", "-c", "core.quotepath=off", *args],
                       capture_output=True, check=True)
    return r.stdout.decode("utf-8")


def write(repo: Path, rel: str, lines: list[str], eol: str = "\r\n") -> None:
    """Файл модуля как у конфигуратора: UTF-8 с BOM, CRLF."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("﻿".encode("utf-8") + (eol.join(lines) + eol).encode("utf-8"))


def commit(repo: Path, message: str = "база") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def new_repo(tmp_path: Path, name: str = "repo") -> Path:
    """Выгрузка с вендорским и нашим документом в базовой ревизии (как New-CheckRepo)."""
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, "Configuration.xml", ["<MetaDataObject/>"])
    write(repo, "Documents/Вендорский/Ext/ObjectModule.bsl",
          ["Процедура ПередЗаписью(Отказ, РежимЗаписи, РежимПроведения)", "\tЗначение = 1;", "КонецПроцедуры"])
    write(repo, "Documents/Вендорский.xml", ["<MetaDataObject/>"])
    write(repo, f"Documents/{PREFIX}Наш/Ext/ObjectModule.bsl",
          ["Процедура ОбработкаПроведения(Отказ, РежимПроведения)", "\tЗначение = 1;", "КонецПроцедуры"])
    write(repo, f"Documents/{PREFIX}Наш.xml", ["<MetaDataObject/>"])
    commit(repo)
    return repo


def registry(*, templates: list[dict] | None = None, exceptions: list[dict] | None = None,
             settings: dict | None = None) -> dict:
    """Реестр, где ид правила вида «код» = ключ проверки (так ассерты читаются сами)."""
    rules = [{"ид": key, "вид": "код", "уровень": c.level, "текст": c.text, "регламент": None,
              "инструмент": "bsl-ls", "реализация": key} for key, c in CATALOG.items()]
    rules.append({"ид": "чужое.1", "вид": "код", "уровень": "Fail", "текст": "правило другого инструмента",
                  "инструмент": "другой"})
    return {"настройки": settings if settings is not None else {"префикс": PREFIX},
            "правила": rules + (templates or []),
            "исключения": exceptions or []}


def hits(result: dict, rule_id: str) -> list[tuple[str, int, str]]:
    return [(f["файл"], f["строка"], f["текст"]) for f in result["находки"] if f["ид"] == rule_id]
