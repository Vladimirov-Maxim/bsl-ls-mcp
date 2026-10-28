"""Сценарии проверок кода задачи: `rules_check` (находки в формате договора проверок)
и `task_methods` (новые и изменённые методы задачи). Каналы (MCP, командная строка)
зовут их одинаково и друг о друге не знают; источник правок и реестр им даёт корень
сборки."""
from __future__ import annotations

from ..domain.checks.bsl_line import split_text
from ..domain.checks.bsl_module import ModuleModel
from ..domain.checks import form_events, query_text
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
    """Модули задачи и схемы компоновки данных задачи (запросы наборов данных)."""
    changes = source.changes()
    files = [f for _, f in _files_with_changes(source, paths, changes)]
    for c in changes:
        if c.status == "D" or not c.path.casefold().endswith((".dcs", ".xml")) or not _wanted(c.path, paths):
            continue
        text = source.new_text(c.path)
        if text is not None and query_text.is_dcs(c.path, text):
            files.append(FileUnderCheck(path=c.path, status=c.status, lines=split_text(text),
                                        mask=source.changed_lines(c)))
    return files, changes


def _files_with_changes(source: ChangeSource, paths: list[str] | None, changes: list):
    for c in changes:
        if c.status == "D" or not c.path.lower().endswith(".bsl") or not _wanted(c.path, paths):
            continue
        text = source.new_text(c.path)
        if text is None:
            continue
        yield c, FileUnderCheck(path=c.path, status=c.status, lines=split_text(text),
                                mask=source.changed_lines(c))


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

    def form_handlers(path: str) -> dict[str, set[str]]:
        if not form_events.is_form_module(path):
            return {}
        for form_path in form_events.form_files_for(path):
            text = source.new_text(form_path)
            if text is not None:
                return form_events.handlers_from_form(text)
        return {}

    findings = check_files(files, rules, own=own_modules((c.status, c.path) for c in changes),
                           common_module=common_module, form_handlers=form_handlers)
    result = finish(findings, list(rules.exceptions), list(rules.problems))
    base = getattr(source, "base_info", None)
    if base:
        result["база"] = base
    return result


def _base_names(source: ChangeSource, change) -> frozenset[str] | None:
    """Имена методов базовой версии файла (casefold): новый файл — пусто (все методы новые);
    базу не удалось прочитать — None (тогда «новый» — по добавленной строке объявления)."""
    if change.status == "A":
        return frozenset()
    old = source.old_text(change)
    if old is None:
        return None
    return frozenset(m.name.casefold() for m in ModuleModel(split_text(old)).methods)


def run_task_methods(source: ChangeSource, *, paths: list[str] | None = None) -> list[dict]:
    """Новые и изменённые методы задачи — `[{файл, модуль, метод, вид, экспорт, строки, статус}]`."""
    out: list[dict] = []
    for c, f in _files_with_changes(source, paths, source.changes()):
        if not f.mask:
            continue          # только окончания строк и т. п. — правки задачи в файле нет
        out += task_methods(f, module_label(f.path), _base_names(source, c))
    return sorted(out, key=lambda r: (r["файл"], r["строки"][0]))
