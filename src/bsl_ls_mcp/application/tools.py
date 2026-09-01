"""Use-case'ы инструментов — намеренно тонкие: резолв имени -> вызов порта -> маппинг.
Без классов-интеракторов: логика вырождается в проброс."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from ..domain import mapper, resolver
from ..domain.models import Complexity
from ..domain.ports import CodeAnalyzer, LspServer
from ..settings import Settings


@dataclass
class Deps:
    lsp: LspServer          # навигация по тёплому индексу (callers/references/...)
    analyzer: CodeAnalyzer  # проверка кода (diagnostics) — пофайлово, без индекса
    settings: Settings


class ResolveError(RuntimeError):
    pass


async def _resolve_pos(deps: Deps, full_name: str) -> resolver.Position:
    """Имя 1С -> позиция. Файлы-кандидаты модуля детерминированы по карте каталогов
    (включая модули форм/команд по маркеру); точную позицию символа даёт сам сервер
    (documentSymbol), без текстового поиска."""
    uris, symbol = resolver.symbol_candidates(deps.settings.workspace, full_name)
    for uri in uris:
        symbols = await deps.lsp.document_symbol(uri)
        pos = mapper.find_symbol_position(symbols, symbol)
        if pos is not None:
            return resolver.Position(uri=uri, line=pos[0], character=pos[1])
    raise ResolveError(f"не удалось разрешить {full_name!r} в позицию")


async def _prepare_item(deps: Deps, full_name: str) -> dict | None:
    pos = await _resolve_pos(deps, full_name)
    items = await deps.lsp.prepare_call_hierarchy(pos.uri, pos.line, pos.character)
    return items[0] if items else None


async def bsl_callers(deps: Deps, full_name: str) -> list[dict]:
    """Кто вызывает функцию (incoming). Анализ влияния «кто вызовет мою правку»."""
    item = await _prepare_item(deps, full_name)
    if item is None:
        return []
    incoming = await deps.lsp.incoming_calls(item)
    refs = [mapper.call_hierarchy_item_to_ref(c["from"]) for c in incoming if "from" in c]
    return [asdict(r) for r in refs]


async def bsl_callees(deps: Deps, full_name: str) -> list[dict]:
    """Кого вызывает функция (outgoing)."""
    item = await _prepare_item(deps, full_name)
    if item is None:
        return []
    outgoing = await deps.lsp.outgoing_calls(item)
    refs = [mapper.call_hierarchy_item_to_ref(c["to"]) for c in outgoing if "to" in c]
    return [asdict(r) for r in refs]


# Порядок важности severity (выше — критичнее). Порог min_severity отсекает по нему.
_SEVERITY_RANK = {"error": 3, "warning": 2, "info": 1, "hint": 0}


def _same_file(uri: str, target: Path) -> bool:
    try:
        return mapper.uri_to_path(uri).resolve() == target
    except (ValueError, OSError):
        return False


def _diag_label(workspace: Path, src_dir: Path, d: dict, fallback: str) -> str:
    """Подпись file: точное имя 1С по пути (если файл в корпусе), иначе — путь
    относительно srcDir (внешние обработки вне корпуса), иначе — fallback."""
    uri = d.get("_src_uri", "")
    label = mapper.analyze_label_from_uri(workspace, uri)
    if label:
        return label
    try:
        return mapper.uri_to_path(uri).relative_to(src_dir).as_posix()
    except (ValueError, OSError):
        return fallback


async def bsl_diagnostics(deps: Deps, module_full_name: str | None = None,
                          path: str | None = None, text: str | None = None,
                          min_severity: str | None = None,
                          code: str | None = None) -> dict:
    """Диагностики кода. Блокирующий гейт developer/reviewer.

    Ровно ОДИН из адресов:
      module_full_name — модуль ПРОИНДЕКСИРОВАННОГО корпуса, напр. 'ОбщийМодуль.МойМодуль'
                         или 'Справочник.X.Форма.Имя' (резолв по дереву workspace);
      path             — ПРОИЗВОЛЬНЫЙ каталог или .bsl-файл ВНЕ корпуса (внешние
                         обработки/отчёты, напр. 'C:\\1c\\work\\<задача>\\Реализация');
      text             — СТРОКА кода 1С (снипет): пишем во временный .bsl и линтуем.
                         Обёртка в процедуру НЕ обязательна — блок операторов линтуется
                         как есть; нужна лишь синтаксическая ЗАВЕРШЁННОСТЬ (все Если/Цикл
                         закрыты, выражения не оборваны — иначе ParseError). Проверки
                         ПОФАЙЛОВЫЕ: без контекста конфигурации (тип модуля, ссылки на
                         объекты не проверяются) — только синтаксис/стиль/качество. Для
                         полной проверки — записать в модуль и звать по имени/path.
                         Подпись file = '<snippet>'. Для снипета по умолчанию отдаём ВСЕ
                         замечания (min_severity='hint'), а не только error+warning —
                         объём мал, а стиль (длина строк, пробелы) как раз интересен.

    min_severity — порог показа. По умолчанию (None): 'warning' для module/path,
                   'hint' (всё) для text. Явное значение переопределяет.

    На крупных модулях (формы) диагностик сотни и стилевой шум (hint/info)
    переполняет бюджет агента. Поэтому по умолчанию (`min_severity='warning'`)
    возвращаем полным списком только error+warning, а остальное СВОРАЧИВАЕМ в
    сводку-счётчики `suppressed.by_code` (ничего не теряя). Агент видит, чего и
    сколько отсеяно, и дозапрашивает детали через `code='Typo'` (все по этому коду,
    любой severity) или `min_severity='hint'` (всё подряд — на свой риск по объёму).

    Возвращает объект:
      {"diagnostics": [<Diagnostic>...],            # показанные, по убыванию severity
       "suppressed": {"total": N, "by_code": [{"code","severity","count"}...]}}
    """
    if sum(x is not None for x in (module_full_name, path, text)) != 1:
        raise ResolveError("укажите РОВНО ОДИН адрес: module_full_name (модуль корпуса) "
                           "| path (каталог/файл вне корпуса) | text (снипет кода)")

    if min_severity is None:
        # снипет мал — показываем ВСЁ (hint/info тоже полезны: длина строк, пробелы);
        # модуль/путь (могут быть сотни диагностик) — прежний порог warning.
        min_severity = "hint" if text is not None else "warning"

    only_file: Path | None = None
    tmp_dir: Path | None = None
    if text is not None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="bsl_snip_"))
        (tmp_dir / "snippet.bsl").write_text(text, encoding="utf-8")
        src_dir, fallback = tmp_dir, "<snippet>"
    elif path is not None:
        p = Path(path)
        # Конфайнмент: путь обязан лежать под workspace или одним из BSL_ALLOWED_ROOTS.
        # Иначе path-режим — оракул ФС (перебор каталогов), утечка содержимого чужих
        # файлов и, для UNC, исходящий SMB с утечкой NTLM-хэша сервисного аккаунта.
        if not resolver.within_roots(p, deps.settings.allowed_roots):
            raise ResolveError(
                f"путь вне разрешённых корней: {path!r}. Разрешены workspace и "
                f"BSL_ALLOWED_ROOTS; UNC-пути (\\\\host\\share) запрещены.")
        if not p.exists():
            raise ResolveError(f"путь не найден: {path!r}")
        src_dir = p if p.is_dir() else p.parent
        only_file = None if p.is_dir() else p.resolve()
        fallback = p.name
    else:
        if module_full_name.count(".") < 1:
            raise ResolveError("ожидался формат Тип.Модуль, напр. 'ОбщийМодуль.МойМодуль'")
        src_dir = resolver.diagnostics_src_dir(deps.settings.workspace, module_full_name)
        if src_dir is None:
            raise ResolveError(f"не найден каталог модуля {module_full_name!r}")
        fallback = module_full_name

    # Проверка кода — через analyze-CLI (разовый java, без тёплого индекса).
    try:
        raw = await deps.analyzer.analyze(src_dir)
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    if only_file is not None:
        raw = [d for d in raw if _same_file(d.get("_src_uri", ""), only_file)]
    ws = deps.settings.workspace
    if tmp_dir is not None:   # снипет: все диагностики под одной подписью '<snippet>'
        items = [mapper.analyze_diagnostic_to_model(fallback, d) for d in raw]
    else:
        items = [mapper.analyze_diagnostic_to_model(_diag_label(ws, src_dir, d, fallback), d)
                 for d in raw]

    # Drill-down: явный запрос по коду → все совпавшие, без отсева по severity и размеру.
    if code is not None:
        shown = sorted((m for m in items if m.code == code),
                       key=lambda m: (-_SEVERITY_RANK.get(m.severity, 0), m.line))
        return {"diagnostics": [asdict(m) for m in shown],
                "suppressed": {"total": 0, "by_code": []}}

    thr = _SEVERITY_RANK.get(min_severity, 2)
    shown = [m for m in items if _SEVERITY_RANK.get(m.severity, 0) >= thr]
    hidden = [m for m in items if _SEVERITY_RANK.get(m.severity, 0) < thr]
    shown.sort(key=lambda m: (-_SEVERITY_RANK.get(m.severity, 0), m.line))

    # Адаптив по размеру: если показанное не влезает в бюджет — сворачиваем НЕ-error
    # (warning) в сводку, ошибки оставляем всегда. Если и одни ошибки за бюджетом —
    # отдаём как есть (ошибки важнее лимита).
    folded_by_size = False
    if len(json.dumps([asdict(m) for m in shown], ensure_ascii=False)) > deps.settings.diag_max_chars:
        non_err = [m for m in shown if m.severity != "error"]
        if non_err:
            shown = [m for m in shown if m.severity == "error"]
            hidden = hidden + non_err
            folded_by_size = True

    by_code = Counter((m.code, m.severity) for m in hidden)
    summary = sorted(
        ({"code": c, "severity": s, "count": n} for (c, s), n in by_code.items()),
        key=lambda x: -x["count"],
    )
    suppressed = {"total": len(hidden), "by_code": summary}
    if folded_by_size:
        suppressed["note"] = ("warning свёрнуты из-за размера ответа; запросите детали "
                              "по коду (code='...') или по конкретной процедуре")
    return {"diagnostics": [asdict(m) for m in shown], "suppressed": suppressed}


def _code_loc(loc: dict) -> dict:
    # читает строку кода из файла → в to_thread, чтобы не блокировать event loop
    return asdict(mapper.location_to_code_location(loc))


async def bsl_definition(deps: Deps, full_name: str) -> list[dict]:
    """Где объявлен символ. full_name: 'Тип.Модуль.Символ'."""
    pos = await _resolve_pos(deps, full_name)
    locs = await deps.lsp.definition(pos.uri, pos.line, pos.character)
    return list(await asyncio.gather(*(asyncio.to_thread(_code_loc, loc) for loc in locs)))


async def bsl_references(deps: Deps, full_name: str) -> list[dict]:
    """Где используется символ (без объявления). full_name: 'Тип.Модуль.Символ'."""
    pos = await _resolve_pos(deps, full_name)
    locs = await deps.lsp.references(pos.uri, pos.line, pos.character)
    return list(await asyncio.gather(*(asyncio.to_thread(_code_loc, loc) for loc in locs)))


async def bsl_complexity(deps: Deps, module_full_name: str,
                         function: str | None = None) -> list[dict]:
    """Когнитивная и цикломатическая сложность методов модуля (метрика ревьюеру).
    module_full_name: 'ОбщийМодуль.МойМодуль'; function (опц.) — только этот метод.
    Числа берутся из code lens сложности (textDocument/codeLens + codeLens/resolve)."""
    if module_full_name.count(".") < 1:
        raise ResolveError("ожидался формат Тип.Модуль, напр. 'ОбщийМодуль.МойМодуль'")
    uri = resolver.module_file_uri(deps.settings.workspace, module_full_name)
    if uri is None:
        raise ResolveError(f"не найден файл модуля {module_full_name!r}")

    # отбираем линзы сложности нужного метода
    lenses = [
        (mapper.lens_complexity_kind(ln), mapper.lens_method_name(ln), ln)
        for ln in await deps.lsp.code_lens(uri)
    ]
    lenses = [(k, m, ln) for k, m, ln in lenses
              if k is not None and (function is None or m == function)]

    # резолвим параллельно (каждый resolve — отдельный round-trip)
    resolved = await asyncio.gather(*(deps.lsp.resolve_code_lens(ln) for _, _, ln in lenses))

    acc: dict[str, dict[str, int]] = {}
    for (kind, method, _), res in zip(lenses, resolved):
        acc.setdefault(method, {})[kind] = mapper.parse_complexity_number(res)

    return [
        asdict(Complexity(
            full_name=f"{module_full_name}.{method}",
            cognitive=vals.get("cognitive"),     # None — если метрика не измерена
            cyclomatic=vals.get("cyclomatic"),
        ))
        for method, vals in acc.items()
    ]


async def bsl_reindex(deps: Deps) -> dict:
    """Полный реиндекс корпуса in-place (после крупных/массовых изменений конфигурации).
    Точечные правки модулей подхватываются сами — это для массовых изменений."""
    await deps.lsp.restart()
    return {"status": "reindexing",
            "note": "индекс пересобирается; следующий вызов подождёт готовности (~1.5 мин на корпусе)"}
