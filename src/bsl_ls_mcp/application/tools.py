"""Use-case'ы инструментов — намеренно тонкие: резолв имени -> вызов порта -> маппинг.
Без классов-интеракторов: логика вырождается в проброс."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from ..domain import line_filter, mapper, resolver, string_calls
from ..domain.checks.bsl_line import split_text
from ..domain.models import Complexity
from ..domain.ports import CodeAnalyzer, LspServer, TextSearch
from ..settings import Settings


@dataclass
class Deps:
    lsp: LspServer          # навигация по тёплому индексу (callers/references/...)
    analyzer: CodeAnalyzer  # проверка кода (diagnostics) — пофайлово, без индекса
    settings: Settings
    search: TextSearch | None = None   # вызовы по строковому имени (bsl_callers)


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
    """Кто вызывает функцию: граф LSP («вызов») + вызовы по строковому имени («строка»).
    Анализ влияния «кто вызовет мою правку»."""
    item = await _prepare_item(deps, full_name)
    out: list[dict] = []
    if item is not None:
        incoming = await deps.lsp.incoming_calls(item)
        out = [{**asdict(mapper.call_hierarchy_item_to_ref(c["from"])), "способ": "вызов"}
               for c in incoming if "from" in c]
    return out + await _string_callers(deps, full_name)


async def _string_callers(deps: Deps, full_name: str) -> list[dict]:
    """Вызывающие по строковому литералу имени метода (ЗИ-34): одна запись на объемлющий
    метод, со списком строк. Кандидаты — файлы, где встречается «Метод"»."""
    names = string_calls.literal_names(full_name)
    if not names or deps.search is None:
        return []
    method = full_name.rsplit(".", 1)[1]
    files = await asyncio.to_thread(deps.search.files_with, f'{method}"')
    found: dict[str, dict] = {}
    for path, text in files:
        calls = string_calls.find_string_calls(split_text(text), names)
        if not calls:
            continue
        type_ru, module = mapper.module_address_from_uri(path.resolve().as_uri())
        for c in calls:
            name = c.method or "<тело модуля>"
            key = f"{type_ru}.{module}.{name}"
            entry = found.setdefault(key, {
                "name": name, "type": type_ru, "full_name": key,
                "kind": {"Функция": "function", "Процедура": "procedure"}.get(c.kind or "", "module"),
                "способ": "строка", "строки": []})
            entry["строки"].append(c.line)
    return sorted(found.values(), key=lambda e: e["full_name"])


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
        return mapper.uri_to_path(uri).resolve().relative_to(Path(src_dir).resolve()).as_posix()
    except (ValueError, OSError):
        return fallback


def _uri_key(uri: str) -> str:
    try:
        return str(mapper.uri_to_path(uri).resolve()).casefold()
    except (ValueError, OSError):
        return uri


def _mirror(workspace: Path, files: tuple[Path, ...], tmp_dir: Path) -> dict[str, str]:
    """Копии файлов в tmp_dir с путём от корня workspace; ключ — копия, значение — URI
    исходного файла (для подписи диагностики)."""
    out: dict[str, str] = {}
    for f in files:
        rel = f.relative_to(workspace)
        dst = tmp_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dst)
        out[str(dst.resolve()).casefold()] = f.resolve().as_uri()
    return out


# Строковый уровень analyze-отчёта по числовому уровню LSP (единая модель — analyze_diagnostic_to_model).
_LSP_SEVERITY = {1: "Error", 2: "Warning", 3: "Information", 4: "Hint"}


def _engine(deps: Deps) -> str:
    """Источник диагностик кода корпуса. Тёплый LSP видит метаданные всей конфигурации —
    без ложных UnknownMember/UnusedLocalVariable на её объектах и реквизитах форм, с
    MissingCommonModuleMethod; analyze видит только переданный каталог."""
    mode = deps.settings.diag_engine
    if mode == "analyze" or deps.lsp is None:
        return "analyze"
    if mode == "lsp" or deps.lsp.is_ready():
        return "lsp"
    return "analyze"


def _in_workspace(deps: Deps, p: Path) -> bool:
    try:
        p.resolve().relative_to(deps.settings.workspace.resolve())
        return True
    except (ValueError, OSError):
        return False


async def _lsp_raw(deps: Deps, files: list[Path]) -> list[dict]:
    """Диагностики файлов из тёплого LSP — в форме записей analyze-отчёта."""
    raw: list[dict] = []
    for f in files:
        uri = f.resolve().as_uri()
        for d in await deps.lsp.diagnostics(uri):
            raw.append({**d, "severity": _LSP_SEVERITY.get(d.get("severity", 1), "Error"),
                        "_src_uri": uri})
    return raw


def _path_key(p: Path) -> str:
    return str(p.resolve()).casefold()


def _scope(files: list[Path], lines: str | None, function: str | None
           ) -> tuple[dict[str, list[tuple[int, int]]], dict]:
    """Зона правок: файл → диапазоны строк, и её описание для ответа."""
    if lines is not None and function is not None:
        raise ResolveError("укажите lines ИЛИ function, не оба сразу")
    if lines is not None:
        if len(files) != 1:
            names = ", ".join(f.name for f in files[:6])
            raise ResolveError(
                f"lines относится к одному файлу, а в цели их {len(files)} ({names}): укажите модуль "
                "('Тип.Объект.МодульОбъекта' / '.МодульМенеджера' / '.Форма.Имя'), path к файлу или function=")
        try:
            ranges = line_filter.parse_lines(lines)
        except ValueError as exc:
            raise ResolveError(str(exc)) from None
        return {_path_key(files[0]): list(ranges)}, {"строки": [list(r) for r in ranges]}
    names = [n.strip() for n in function.split(",") if n.strip()]
    if not names:
        raise ResolveError("function: пустое имя метода")
    scope: dict[str, list[tuple[int, int]]] = {}
    found: dict[str, list[int]] = {}
    for f in files:
        try:
            text = f.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError:
            continue
        for name, (lo, hi) in line_filter.method_ranges(split_text(text), names).items():
            scope.setdefault(_path_key(f), []).append((lo, hi))
            label = name if len(files) == 1 else f"{f.name}:{name}"
            found[label] = [lo, hi]
    seen = {k.rsplit(":", 1)[-1].casefold() for k in found}
    missing = [n for n in names if n.casefold() not in seen]
    if missing:
        raise ResolveError(f"метод не найден в цели: {', '.join(missing)}")
    return scope, {"методы": found}


def _apply_scope(raw: list[dict], scope: dict[str, list[tuple[int, int]]] | None) -> list[dict]:
    if scope is None:
        return raw
    out = []
    for d in raw:
        ranges = scope.get(_uri_key(d.get("_src_uri", "")))
        line = (d.get("range", {}).get("start", {}).get("line", 0)) + 1
        if ranges and line_filter.in_ranges(line, ranges):
            out.append(d)
    return out


async def bsl_diagnostics(deps: Deps, module_full_name: str | None = None,
                          path: str | None = None, text: str | None = None,
                          min_severity: str | None = None,
                          code: str | None = None, lines: str | None = None,
                          function: str | None = None) -> dict:
    """Диагностики кода. Блокирующий гейт developer/reviewer.

    Ровно ОДИН из адресов:
      module_full_name — модуль ПРОИНДЕКСИРОВАННОГО корпуса, напр. 'ОбщийМодуль.МойМодуль'
                         или 'Справочник.X.Форма.Имя' (резолв по дереву workspace);
      path             — ПРОИЗВОЛЬНЫЙ локальный каталог или .bsl-файл, в т.ч. вне
                         корпуса (внешние обработки/отчёты, расширения, напр.
                         'C:\\1c\\work\\<задача>\\Реализация'); UNC-пути запрещены;
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

    Источник (поле "источник"): для кода корпуса (module_full_name и path внутри
    workspace) — тёплый LSP, когда индекс готов: он видит метаданные конфигурации. Пока
    индекс строится — пакетный analyze (без контекста конфигурации: возможны ложные
    UnknownMember на её объектах), сервер при этом прогревается в фоне. Код вне workspace
    и снипет — всегда analyze. BSL_DIAG_ENGINE=analyze|lsp фиксирует источник.

    Зона правок — lines ИЛИ function: только диагностики, начинающиеся в ней, по умолчанию
    ВСЕ уровни (min_severity='hint') и без сворачивания по размеру:
      lines    — '2660-2781' | '10-20,35' (1-based, включительно); цель — ОДИН файл
                 (модуль 'Тип.Х.МодульОбъекта', форма, path к файлу, снипет);
      function — имя метода или несколько через запятую; границы — от директив (&НаСервере)
                 до КонецПроцедуры/КонецФункции; ищется во всех модулях цели.

    Возвращает объект:
      {"diagnostics": [<Diagnostic>...],            # показанные, по убыванию severity
       "suppressed": {"total": N, "by_code": [{"code","severity","count"}...]},
       "источник": {"движок": "lsp" | "analyze", "примечание"?: "..."}}
    """
    if sum(x is not None for x in (module_full_name, path, text)) != 1:
        raise ResolveError("укажите РОВНО ОДИН адрес: module_full_name (модуль корпуса) "
                           "| path (каталог/файл вне корпуса) | text (снипет кода)")

    if min_severity is None:
        # снипет и зона правок малы — показываем ВСЁ (hint/info тоже полезны: длина строк,
        # пробелы); модуль/путь целиком (сотни диагностик) — прежний порог warning.
        zoned = lines is not None or function is not None
        min_severity = "hint" if text is not None or zoned else "warning"

    only_file: Path | None = None
    tmp_dir: Path | None = None
    mirror: dict[str, str] = {}
    if text is not None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="bsl_snip_"))
        (tmp_dir / "snippet.bsl").write_text(text, encoding="utf-8")
        src_dir, fallback = tmp_dir, "<snippet>"
    elif path is not None:
        p = Path(path)
        # Любой локальный путь: диагностики нужны и коду вне корпуса (внешние обработки,
        # расширения). UNC запрещён — исходящий SMB уводит NTLM-хэш сервисного аккаунта.
        if not resolver.is_local_path(p):
            raise ResolveError(f"UNC-пути (\\\\host\\share) запрещены: {path!r}")
        if not p.exists():
            raise ResolveError(f"путь не найден: {path!r}")
        src_dir = p if p.is_dir() else p.parent
        only_file = None if p.is_dir() else p.resolve()
        fallback = p.name
    else:
        if module_full_name.count(".") < 1:
            raise ResolveError("ожидался формат Тип.Модуль, напр. 'ОбщийМодуль.МойМодуль'")
        target = resolver.diagnostics_target(deps.settings.workspace, module_full_name)
        if target is None:
            raise ResolveError(f"не найден каталог модуля {module_full_name!r}")
        src_dir, fallback = target.src_dir, module_full_name

    # Зона правок: файлы цели → диапазоны строк (до выбора источника — ошибка адреса раньше)
    scope: dict[str, list[tuple[int, int]]] | None = None
    zone: dict | None = None
    if lines is not None or function is not None:
        try:
            if text is not None:
                zone_files = [tmp_dir / "snippet.bsl"]
            elif module_full_name is not None and target.files is not None:
                zone_files = list(target.files)
            elif only_file is not None:
                zone_files = [only_file]
            else:
                zone_files = sorted(src_dir.rglob("*.bsl"))
            scope, zone = _scope(zone_files, lines, function)
        except ResolveError:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    # Код корпуса — из тёплого LSP (контекст конфигурации), если он готов.
    note: str | None = None
    if text is None and (module_full_name is not None or _in_workspace(deps, src_dir)):
        if module_full_name is not None and target.files is not None:
            files = list(target.files)
        elif only_file is not None:
            files = [only_file]
        else:
            files = sorted(src_dir.rglob("*.bsl"))
        engine = _engine(deps)
        if len(files) > deps.settings.diag_lsp_max_files:
            note = f"файлов больше {deps.settings.diag_lsp_max_files} — пакетный analyze"
        elif engine == "lsp":
            try:
                raw = _apply_scope(await _lsp_raw(deps, files), scope)
                new = [f.name for f in files if deps.lsp.is_new(f.resolve().as_uri())]
                if new:
                    note = ("файл создан после индексации — метаданные его объекта сервер не знает "
                            "(проверки, зависящие от типа модуля, неполны); полный контекст — после bsl_reindex")
                return _finish(deps, raw, src_dir, fallback, None, code, min_severity, "lsp", note, zone)
            except (asyncio.TimeoutError, ConnectionError, RuntimeError) as exc:
                note = f"LSP не ответил ({type(exc).__name__}) — пакетный analyze"
        elif deps.settings.diag_engine == "auto" and deps.lsp is not None:
            deps.lsp.warm_up()
            note = ("индекс строится — пакетный analyze без контекста конфигурации: "
                    "UnknownMember на её объектах может быть ложным")

    if module_full_name is not None and target.files is not None:
        # Объект EDT: модули лежат рядом с Forms/ и Commands/ — отдаём analyze только
        # их, копией с тем же путём от корня, и возвращаем подписи к исходным файлам.
        tmp_dir = Path(tempfile.mkdtemp(prefix="bsl_obj_"))
        mirror = _mirror(deps.settings.workspace, target.files, tmp_dir)
        src_dir = tmp_dir

    # Проверка кода — через analyze-CLI (разовый java, без тёплого индекса).
    try:
        raw = await deps.analyzer.analyze(src_dir)
        if mirror:   # пока копии на месте: resolve() несуществующего пути не раскрыл бы 8.3-имена
            raw = [{**d, "_src_uri": mirror.get(_uri_key(d.get("_src_uri", "")), d.get("_src_uri", ""))}
                   for d in raw]
            src_dir = target.src_dir
        raw = _apply_scope(raw, scope)   # пока копии и снипет на месте (ключи — раскрытые пути)
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    if only_file is not None:
        raw = [d for d in raw if _same_file(d.get("_src_uri", ""), only_file)]
    return _finish(deps, raw, src_dir, fallback, text, code, min_severity, "analyze", note, zone)


def _override_severity(items: list, overrides: tuple[tuple[str, str], ...]) -> tuple[list, list[dict]]:
    """Переназначить уровни по настройке (BSL_SEVERITY_OVERRIDES) — до порога и сворачивания.
    Возвращает новые записи и сводку для ответа: что, с какого уровня, на какой, сколько."""
    if not overrides:
        return items, []
    target = dict(overrides)
    counts: Counter = Counter()
    out = []
    for m in items:
        level = target.get(m.code)
        if level is not None and level != m.severity:
            counts[(m.code, m.severity, level)] += 1
            m = replace(m, severity=level)
        out.append(m)
    changed = [{"код": c, "было": was, "стало": now, "число": n}
               for (c, was, now), n in sorted(counts.items())]
    return out, changed


def _finish(deps: Deps, raw: list[dict], src_dir: Path, fallback: str, text: str | None,
            code: str | None, min_severity: str, engine: str, note: str | None,
            zone: dict | None = None) -> dict:
    """Записи диагностик → ответ: подписи, порог, сворачивание по размеру, источник."""
    source = {"движок": engine, **({"примечание": note} if note else {})}
    if zone:
        source["фильтр"] = zone
    ws = deps.settings.workspace
    if text is not None:      # снипет: все диагностики под одной подписью '<snippet>'
        items = [mapper.analyze_diagnostic_to_model(fallback, d) for d in raw]
    else:
        items = [mapper.analyze_diagnostic_to_model(_diag_label(ws, src_dir, d, fallback), d)
                 for d in raw]
    items, changed = _override_severity(items, deps.settings.severity_overrides)
    if changed:
        source["переназначено"] = changed

    # Drill-down: явный запрос по коду → все совпавшие, без отсева по severity и размеру.
    if code is not None:
        shown = sorted((m for m in items if m.code == code),
                       key=lambda m: (-_SEVERITY_RANK.get(m.severity, 0), m.line))
        return {"diagnostics": [asdict(m) for m in shown],
                "suppressed": {"total": 0, "by_code": []}, "источник": source}

    thr = _SEVERITY_RANK.get(min_severity, 2)
    shown = [m for m in items if _SEVERITY_RANK.get(m.severity, 0) >= thr]
    hidden = [m for m in items if _SEVERITY_RANK.get(m.severity, 0) < thr]
    shown.sort(key=lambda m: (-_SEVERITY_RANK.get(m.severity, 0), m.line))

    # Адаптив по размеру: если показанное не влезает в бюджет — сворачиваем НЕ-error
    # (warning) в сводку, ошибки оставляем всегда. Если и одни ошибки за бюджетом —
    # отдаём как есть (ошибки важнее лимита).
    folded_by_size = False
    if zone is None and len(json.dumps([asdict(m) for m in shown], ensure_ascii=False)) > deps.settings.diag_max_chars:
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
    return {"diagnostics": [asdict(m) for m in shown], "suppressed": suppressed, "источник": source}


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
