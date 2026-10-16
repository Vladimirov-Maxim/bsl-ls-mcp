"""Привязки обработчиков событий формы: из описания формы (EDT `Form.form`, конфигуратор
`Form.xml`) и из кода модуля (`Элементы.Х.УстановитьДействие("Событие", "Обработчик")`).
Pure: на входе тексты, на выходе «обработчик → события»."""
from __future__ import annotations

import re

from .bsl_line import WORD

# EDT: <handlers><event>StartChoice</event><name>ПолеНачалоВыбора</name></handlers>
_EDT = re.compile(r"<handlers>\s*<event>([^<]+)</event>\s*<name>([^<]+)</name>", re.IGNORECASE)
# Конфигуратор: <Event name="StartChoice">ПолеНачалоВыбора</Event>
_DESIGNER = re.compile(r'<Event\s+name="([^"]+)"[^>]*>\s*([^<\s][^<]*?)\s*</Event>', re.IGNORECASE)
# Программное назначение — оба написания метода
_SET_ACTION = re.compile(r'(?:УстановитьДействие|SetAction)\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"', re.IGNORECASE)


def handlers_from_form(xml: str) -> dict[str, set[str]]:
    """Обработчик (casefold) → имена событий (как в описании формы)."""
    out: dict[str, set[str]] = {}
    for rx in (_EDT, _DESIGNER):
        for event, handler in rx.findall(xml or ""):
            out.setdefault(handler.strip().casefold(), set()).add(event.strip())
    return out


def handlers_from_code(lines: list[str]) -> dict[str, set[str]]:
    """Обработчики, назначенные в коде модуля строковыми литералами."""
    out: dict[str, set[str]] = {}
    for line in lines:
        if line.lstrip().startswith("//"):
            continue
        for event, handler in _SET_ACTION.findall(line):
            out.setdefault(handler.strip().casefold(), set()).add(event.strip())
    return out


def merge(*maps: dict[str, set[str]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for m in maps:
        for k, v in m.items():
            out.setdefault(k, set()).update(v)
    return out


def form_files_for(module_path: str) -> list[str]:
    """Пути описания формы для пути модуля формы (оба формата, с префиксом корня)."""
    p = module_path.replace("\\", "/")
    out = []
    if p.endswith("/Ext/Form/Module.bsl"):                       # конфигуратор
        out.append(p[: -len("/Form/Module.bsl")] + "/Form.xml")
    elif p.endswith("/Module.bsl"):                              # EDT
        out.append(p[: -len("Module.bsl")] + "Form.form")
    return out


def is_form_module(path: str) -> bool:
    p = "/" + path.replace("\\", "/")
    return p.endswith("/Module.bsl") and ("/Forms/" in p or "/CommonForms/" in p)


CALL = re.compile(rf"(?<![\wА-Яа-яЁё.])({WORD}+)\s*\(")
