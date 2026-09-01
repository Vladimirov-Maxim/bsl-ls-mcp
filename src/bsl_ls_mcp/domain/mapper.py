"""LSP-объекты -> доменные модели (русские имена 1С, как в ПолноеИмя()).

Тип/полное имя строятся словарём (one_c_naming + one_c_config_path) —
единая идентичность имён, полный охват типов."""
from __future__ import annotations

import re
import urllib.parse
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from .models import CodeLocation, Diagnostic, SymbolRef
from .one_c_config_path import _OBJECT_TYPE_MAP, OneCConfigPathParser
from .one_c_naming import type_en_to_ru

# LSP SymbolKind (число) -> вид (строка). 6=Method, 12=Function.
_KIND = {5: "class", 6: "procedure", 9: "constructor", 12: "function"}

_SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}


def _type_and_module_from_uri(uri: str) -> tuple[str, str]:
    """file:///.../CommonModules/МойМодуль/Ext/Module.bsl -> ('ОбщийМодуль', 'МойМодуль').
    Ищет в пути любую type-директорию CR-выгрузки (полный охват _OBJECT_TYPE_MAP).
    Для модулей форм/команд возвращает АДРЕС с сегментом формы/команды
    (`Объект.Форма.Имя`), чтобы full_name из ответа был валиден на входе (round-trip)."""
    path = PurePosixPath(urllib.parse.unquote(urllib.parse.urlparse(uri).path))
    parts = path.parts
    for i, part in enumerate(parts):
        type_en = _OBJECT_TYPE_MAP.get(part)
        if type_en is not None:
            type_ru = type_en_to_ru(type_en) or type_en
            obj = parts[i + 1] if i + 1 < len(parts) else "?"
            # .../<TypeDir>/<Obj>/Forms/<ИмяФормы>/Ext/Form/Module.bsl
            if i + 3 < len(parts) and parts[i + 2] == "Forms":
                return type_ru, f"{obj}.Форма.{parts[i + 3]}"
            # .../<TypeDir>/<Obj>/Commands/<ИмяКоманды>/Ext/CommandModule.bsl
            if i + 3 < len(parts) and parts[i + 2] == "Commands":
                return type_ru, f"{obj}.Команда.{parts[i + 3]}"
            return type_ru, obj
    return "?", "?"


def find_symbol_position(symbols: list[dict[str, Any]], name: str) -> tuple[int, int] | None:
    """Позиция объявления символа в дереве documentSymbol (DocumentSymbol —
    с children/selectionRange; либо плоский SymbolInformation — location).
    Имя сравниваем точно (а если сервер вернул сигнатуру 'Имя(...)' — по части до '(')."""
    for sym in symbols or []:
        sname = str(sym.get("name", ""))
        if sname == name or sname.split("(", 1)[0].strip() == name:
            rng = (sym.get("selectionRange") or sym.get("range")
                   or sym.get("location", {}).get("range") or {})
            start = rng.get("start", {})
            return start.get("line", 0), start.get("character", 0)
        child = find_symbol_position(sym.get("children", []), name)
        if child is not None:
            return child
    return None


def call_hierarchy_item_to_ref(item: dict[str, Any]) -> SymbolRef:
    type_ru, module = _type_and_module_from_uri(item.get("uri", ""))
    name = item.get("name", "?")
    return SymbolRef(
        name=name,
        type=type_ru,
        full_name=f"{type_ru}.{module}.{name}",
        kind=_KIND.get(item.get("kind"), "unknown"),
    )


def uri_to_path(uri: str) -> Path:
    """file:///C:/My%20Sources/x.bsl -> Path('C:/My Sources/x.bsl')."""
    p = urllib.parse.unquote(urllib.parse.urlparse(uri).path)
    if p.startswith("/") and len(p) > 2 and p[2] == ":":  # /C:/... -> C:/...
        p = p[1:]
    return Path(p)


@lru_cache(maxsize=128)
def _file_lines(path_str: str, mtime: float) -> tuple[str, ...]:
    """Строки файла, кэш по (путь, mtime) — без переоткрытия на каждую ссылку;
    кэш инвалидируется при правке файла (меняется mtime)."""
    try:
        return tuple(Path(path_str).read_text(encoding="utf-8").splitlines())
    except OSError:
        return ()


def _line_text(uri: str, line0: int) -> str:
    path = uri_to_path(uri)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    lines = _file_lines(str(path), mtime)
    return lines[line0].strip() if 0 <= line0 < len(lines) else ""


def location_to_code_location(loc: dict[str, Any]) -> CodeLocation:
    """LSP Location | LocationLink -> CodeLocation (имена 1С + строка кода)."""
    uri = loc.get("uri") or loc.get("targetUri", "")
    rng = loc.get("range") or loc.get("targetRange") or {}
    line0 = rng.get("start", {}).get("line", 0)
    type_ru, module = _type_and_module_from_uri(uri)
    return CodeLocation(
        type=type_ru,
        module=module,
        full_name=f"{type_ru}.{module}",
        line=line0 + 1,
        text=_line_text(uri, line0),
    )


_NUM_RE = re.compile(r"(\d+)")


def lens_complexity_kind(lens: dict[str, Any]) -> str | None:
    """data.id линзы → 'cognitive' | 'cyclomatic' | None (не линза сложности)."""
    idv = str((lens.get("data") or {}).get("id", "")).lower()
    if "cognitive" in idv:
        return "cognitive"
    if "cyclomatic" in idv:
        return "cyclomatic"
    return None


def lens_method_name(lens: dict[str, Any]) -> str:
    return str((lens.get("data") or {}).get("methodName", "?"))


def parse_complexity_number(resolved_lens: dict[str, Any]) -> int:
    """Число из command.title ('Когнитивная сложность: 5' → 5)."""
    title = (resolved_lens.get("command") or {}).get("title", "")
    m = _NUM_RE.search(title)
    return int(m.group(1)) if m else 0


def diagnostic_to_model(full_name: str, d: dict[str, Any]) -> Diagnostic:
    start = d.get("range", {}).get("start", {})
    return Diagnostic(
        file=full_name,
        line=start.get("line", 0) + 1,  # 1-based для человека
        code=str(d.get("code", "")),
        severity=_SEVERITY.get(d.get("severity", 1), "error"),
        message=d.get("message", ""),
    )


# module_kind (из разбора пути) → русский хвост подписи модуля. Детерминированная карта.
_MODULE_KIND_RU = {
    "ManagerModule": "МодульМенеджера",
    "ObjectModule": "МодульОбъекта",
    "RecordSetModule": "МодульНабораЗаписей",
    "ValueManagerModule": "МодульМенеджераЗначения",
    "CommandModule": "МодульКоманды",
}
_PATH_PARSER = OneCConfigPathParser()


def analyze_label_from_uri(workspace: Path, uri: str) -> str | None:
    """Точная подпись модуля из ПУТИ файла отчёта analyze (mdoRef) — детерминированно,
    через OneCConfigPathParser (разбор пути, НЕ имени). Различает менеджер/объект/форму:
      .../Catalogs/X/Ext/ManagerModule.bsl      -> 'Справочник.X.МодульМенеджера'
      .../Catalogs/X/Ext/ObjectModule.bsl       -> 'Справочник.X.МодульОбъекта'
      .../Catalogs/X/Forms/Y/Ext/Form/Module.bsl-> 'Справочник.X.Форма.Y'
      .../CommonModules/X/Ext/Module.bsl        -> 'ОбщийМодуль.X'
    None — путь не под workspace или не распознан (вызывающий берёт переданное имя)."""
    if not uri:
        return None
    try:
        ctx = _PATH_PARSER.parse(root=workspace, file_path=uri_to_path(uri))
    except (ValueError, OSError):
        return None  # путь не под workspace
    if not ctx.object_type or not ctx.object_name:
        return None
    type_ru = type_en_to_ru(ctx.object_type)
    if type_ru is None:
        return None
    if ctx.object_type == "CommonModule":
        return f"{type_ru}.{ctx.object_name}"
    if ctx.form_name:
        return f"{type_ru}.{ctx.object_name}.Форма.{ctx.form_name}"
    kind_ru = _MODULE_KIND_RU.get(ctx.module_kind or "")
    return f"{type_ru}.{ctx.object_name}.{kind_ru}" if kind_ru else f"{type_ru}.{ctx.object_name}"


# analyze-CLI отдаёт severity СТРОКОЙ ("Error"/"Warning"/...), LSP — числом. Своя карта.
_ANALYZE_SEVERITY = {"error": "error", "warning": "warning",
                     "information": "info", "info": "info", "hint": "hint"}


def analyze_diagnostic_to_model(file_label: str, d: dict[str, Any]) -> Diagnostic:
    """Сырая диагностика из analyze-JSON → доменная модель (severity приходит строкой)."""
    start = d.get("range", {}).get("start", {})
    sev = str(d.get("severity", "error")).lower()
    return Diagnostic(
        file=file_label,
        line=start.get("line", 0) + 1,
        code=str(d.get("code", "")),
        severity=_ANALYZE_SEVERITY.get(sev, "error"),
        message=d.get("message", ""),
    )
