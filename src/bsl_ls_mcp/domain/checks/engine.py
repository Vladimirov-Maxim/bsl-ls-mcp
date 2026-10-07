"""Исполнение правил над файлами задачи: тексты и маски → находки. Чисто: откуда
взялись тексты (git, ревизия, пара каталогов) — забота сценария и инфраструктуры."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .bsl_module import ModuleModel
from .code_rules import FileContext
from .findings import Finding
from .registry import RuleSet

# Описание нового общего модуля: `CommonModules/<Имя>.xml` (конфигуратор) или
# `CommonModules/<Имя>/<Имя>.mdo` (EDT), возможно под префиксом корня конфигурации.
_OWN_COMMON_MODULE = re.compile(r"(?:^|/)CommonModules/(?:([^/]+)\.xml|([^/]+)/\2\.mdo)$",
                                re.IGNORECASE)


@dataclass(frozen=True)
class FileUnderCheck:
    path: str                 # от корня выгрузки, прямые косые
    status: str               # "A" | "M" (удалённые файлы построчным правилам не нужны)
    lines: list[str]          # новая версия
    mask: frozenset[int]      # строки задачи


def own_modules(changes: Iterable[tuple[str, str]]) -> frozenset[str]:
    """Общие модули, созданные задачей (новое описание общего модуля), — без учёта регистра."""
    out = set()
    for status, path in changes:
        m = _OWN_COMMON_MODULE.match(path)
        if status == "A" and m:
            out.add((m.group(1) or m.group(2)).casefold())
    return frozenset(out)


def check_files(files: Iterable[FileUnderCheck], rules: RuleSet, *,
                own: frozenset[str] = frozenset(),
                common_module: Callable[[str], ModuleModel | None] = lambda name: None) -> list[Finding]:
    findings: list[Finding] = []
    for f in files:
        if not f.mask:
            continue
        model = ModuleModel(f.lines)
        templates = [t for t in rules.templates if t.applies_to(f.path)]
        if templates:
            for n in sorted(f.mask):
                if n > len(f.lines):
                    continue
                raw = f.lines[n - 1]
                if not raw.strip():
                    continue
                parts = model.parts[n - 1]
                for t in templates:
                    if t.hits(parts, raw):
                        findings.append(Finding(t.rule_id, t.level, f.path, n, t.text, t.regulation))
        ctx = FileContext(path=f.path, model=model, mask=f.mask, knowledge=rules.knowledge,
                          own_modules=own, common_module=common_module)
        for rule in rules.code_rules:
            ctx.params = rule.params
            for hit in rule.check.run(ctx):
                text = f"{rule.text}: {hit.detail}" if hit.detail else rule.text
                findings.append(Finding(rule.rule_id, rule.level, f.path, hit.line, text, rule.regulation))
    return findings


def task_methods(f: FileUnderCheck, label: str) -> list[dict]:
    """Методы, которых касается задача: «новый» — строка объявления добавлена задачей
    (в новом файле — все методы), «изменён» — правка внутри существующего метода."""
    out = []
    for m in ModuleModel(f.lines).methods:
        if m.start in f.mask:
            status = "новый"
        elif any(m.start < n <= m.end for n in f.mask):
            status = "изменён"
        else:
            continue
        out.append({"файл": f.path, "модуль": label, "метод": m.name, "вид": m.kind,
                    "экспорт": m.export, "строки": [m.start, m.end], "статус": status})
    return out
