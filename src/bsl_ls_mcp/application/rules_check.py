"""Сценарии проверок кода задачи: `rules_check` (находки в формате договора проверок)
и `task_methods` (новые и изменённые методы задачи). Каналы (MCP, командная строка)
зовут их одинаково и друг о друге не знают; источник правок и реестр им даёт корень
сборки."""
from __future__ import annotations

from ..domain.checks.bsl_line import split_text
from ..domain.checks.bsl_module import ModuleModel
from ..domain.checks.engine import FileUnderCheck, check_files, own_modules, task_methods
from ..domain.checks.findings import finish
from ..domain.checks.module_names import common_module_paths, module_label
from ..domain.checks.registry import default_ruleset, ruleset_from_registry
from ..domain.ports import ChangeSource


class InputError(ValueError):
    """Неверный вход сценария — понятным текстом для вызывающего."""


def source_kind(repo: str | None, baseline: str | None, target: str | None) -> str:
    """Ровно один источник: `repo` или пара `baseline` + `target`."""
    if bool(baseline) != bool(target):
        raise InputError("baseline и target задаются только вместе")
    if repo and baseline:
        raise InputError("укажите либо repo, либо пару baseline + target — не всё сразу")
    if not repo and not baseline:
        raise InputError("укажите repo (git-репозиторий выгрузки) или пару baseline + target")
    return "git" if repo else "каталоги"


def _wanted(path: str, paths: list[str] | None) -> bool:
    if not paths:
        return True
    p = path.casefold()
    for raw in paths:
        q = raw.replace("\\", "/").strip().strip("/").casefold()
        if q and (p == q or p.startswith(q + "/")):
            return True
    return False


def _files(source: ChangeSource, paths: list[str] | None) -> tuple[list[FileUnderCheck], list]:
    changes = source.changes()
    files = []
    for c in changes:
        if c.status == "D" or not c.path.lower().endswith(".bsl") or not _wanted(c.path, paths):
            continue
        text = source.new_text(c.path)
        if text is None:
            continue
        files.append(FileUnderCheck(path=c.path, status=c.status, lines=split_text(text),
                                    mask=source.changed_lines(c)))
    return files, changes


def run_rules_check(source: ChangeSource, registry: dict | None, *,
                    paths: list[str] | None = None, only: list[str] | None = None) -> dict:
    """Правила инструмента над правками задачи → `{находки, исключены, пропущено}`."""
    rules = (ruleset_from_registry(registry) if registry is not None else default_ruleset()).only(only)
    files, changes = _files(source, paths)
    cache: dict[str, ModuleModel | None] = {}

    prefixes = tuple(getattr(source, "config_prefixes", ("",)))

    def common_module(name: str) -> ModuleModel | None:
        key = name.casefold()
        if key not in cache:
            text = None
            for path in common_module_paths(name, prefixes):
                text = source.new_text(path)
                if text is not None:
                    break
            cache[key] = ModuleModel(split_text(text)) if text is not None else None
        return cache[key]

    findings = check_files(files, rules, own=own_modules((c.status, c.path) for c in changes),
                           common_module=common_module)
    result = finish(findings, list(rules.exceptions), list(rules.problems))
    base = getattr(source, "base_info", None)
    if base:
        result["база"] = base
    return result


def run_task_methods(source: ChangeSource, *, paths: list[str] | None = None) -> list[dict]:
    """Новые и изменённые методы задачи — `[{файл, модуль, метод, вид, экспорт, строки, статус}]`."""
    files, _ = _files(source, paths)
    out: list[dict] = []
    for f in files:
        out += task_methods(f, module_label(f.path))
    return sorted(out, key=lambda r: (r["файл"], r["строки"][0]))
