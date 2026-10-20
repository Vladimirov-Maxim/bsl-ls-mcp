"""Сценарий `diagnostics`: диагностики BSL LS по модулям, изменённым задачей, — только строки
задачи, уровни error и warning, в формате договора проверок (как у `rules_check`).

Источник правок тот же (git: база ↔ рабочая копия или ревизия; пара каталогов), тексты берутся
из него, а не с диска — ревизии на диске может не быть. Модули задачи вместе с описаниями их
объектов и форм раскладываются во временный мини-проект (`domain.task_project`), по нему —
analyze: так BSL LS знает тип модуля и реквизиты формы, без тёплого индекса и без всей
конфигурации. Межмодульного контекста нет: несуществующий метод общего модуля
(`MissingCommonModuleMethod`) здесь не виден — это LSP-проверка `bsl_diagnostics`."""
from __future__ import annotations

import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from ..domain import external_objects as eo
from ..domain import mapper
from ..domain import task_project as tp
from ..domain.checks.findings import Finding, RegistryException, Skipped, finish
from ..domain.ports import ChangeSource, CodeAnalyzer
from .rules_check import _files_with_changes

_LEVEL = {"error": "Fail", "warning": "Warn"}


def _key(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _uri_key(uri: str) -> str:
    try:
        return _key(mapper.uri_to_path(uri))
    except ValueError:
        return uri


def _project_of(source: ChangeSource, path: str, prefixes: tuple[str, ...]) -> tuple[tp.Project, str] | None:
    """Мини-проект модуля и путь модуля от корня конфигурации; None — проект не распознан."""
    prefix, rel = tp.split_prefix(path, prefixes)
    conf = tp.configuration_paths(prefix)
    if source.new_text(conf[tp.EDT]) is not None:
        return tp.Project(prefix, tp.EDT), rel
    if source.new_text(conf[tp.DESIGNER]) is not None:
        return tp.Project(prefix, tp.DESIGNER), rel
    for desc, name, edt in tp.external_candidates(rel):
        text = source.new_text(prefix + desc)
        kind = None if text is None else (eo.kind_of_edt(text) if edt else eo.kind_of_designer(text))
        if kind is not None:
            return tp.Project(prefix, tp.EDT if edt else tp.DESIGNER, kind, name), rel
    return None


def _write(dst: Path, text: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(text.encode("utf-8"))


def _materialize(source: ChangeSource, project: tp.Project, modules: list[str], root: Path) -> dict[str, str]:
    """Разложить проект в root. Возвращает {ключ копии модуля → путь модуля в репозитории}."""
    conf = tp.configuration_paths(project.prefix)
    if project.external is not None:
        desc = tp.external_candidates(modules[0][len(project.prefix):])
        root_desc = next(d for d, name, edt in desc if name == project.name and edt == (project.fmt == tp.EDT))
        path, text = tp.external_configuration(project, source.new_text(project.prefix + root_desc) or "")
        _write(root / path, text)
    elif project.fmt == tp.EDT:
        _write(root / "src" / "Configuration" / "Configuration.mdo", source.new_text(conf[tp.EDT]) or "")
    else:
        _write(root / "Configuration.xml", source.new_text(conf[tp.DESIGNER]) or "")
    if project.fmt == tp.EDT:
        _write(root / "DT-INF" / "PROJECT.PMF", source.new_text(conf["pmf"]) or tp.DEFAULT_PMF)

    copies: dict[str, str] = {}
    written: set[str] = set()
    for path in modules:
        rel = path[len(project.prefix):]
        for desc in tp.descriptions(rel, project):
            if desc in written:
                continue
            written.add(desc)
            text = source.new_text(project.prefix + desc)
            if text is not None:
                _write(root / tp.project_path(desc, project), tp.transform(text, project))
        dst = root / tp.project_path(rel, project)
        _write(dst, source.new_text(path) or "")
        copies[_key(dst)] = path
    return copies


async def run_diagnostics_check(source: ChangeSource, analyzer: CodeAnalyzer, *,
                                paths: list[str] | None = None,
                                severity_overrides: tuple[tuple[str, str], ...] = (),
                                exceptions: list[RegistryException] | None = None) -> dict:
    """Диагностики BSL LS по строкам задачи → `{находки, исключены, пропущено}` (+ `база`).
    `ид` находки — код диагностики, уровень: error → Fail, warning → Warn (после переназначений
    `BSL_SEVERITY_OVERRIDES`; `UnknownMember` по умолчанию info — в находки не идёт)."""
    prefixes = tuple(getattr(source, "config_prefixes", ("",)))
    masks: dict[str, frozenset[int]] = {}
    groups: dict[tp.Project, list[str]] = defaultdict(list)
    skipped: list[Skipped] = []
    for _, f in _files_with_changes(source, paths, source.changes()):
        if not f.mask:
            continue                       # только окончания строк — правки задачи в файле нет
        found = _project_of(source, f.path, prefixes)
        if found is None:
            skipped.append(Skipped(f"диагностики {f.path}", "не найдено описание конфигурации или внешнего объекта"))
            continue
        groups[found[0]].append(f.path)
        masks[f.path] = f.mask

    overrides = dict(severity_overrides)
    findings: list[Finding] = []
    for project, modules in groups.items():
        root = Path(tempfile.mkdtemp(prefix="bsl_task_"))
        try:
            copies = _materialize(source, project, modules, root)
            raw = await analyzer.analyze(root)
            for d in raw:
                path = copies.get(_uri_key(d.get("_src_uri", "")))
                if path is None:
                    continue
                m = mapper.analyze_diagnostic_to_model(path, d)
                level = _LEVEL.get(overrides.get(m.code, m.severity))
                if level is None or m.line not in masks[path]:
                    continue
                findings.append(Finding(m.code, level, path, m.line, m.message, None))
        except Exception as exc:          # analyze упал — называем, а не теряем молча
            skipped.append(Skipped(f"диагностики {project.prefix or '.'} ({len(modules)} модулей)",
                                   f"{type(exc).__name__}: {exc}"))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    result = finish(findings, list(exceptions or ()), skipped)
    base = getattr(source, "base_info", None)
    if base:
        result["база"] = base
    return result
