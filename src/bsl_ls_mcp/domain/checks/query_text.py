"""Тексты запросов в модуле и в схеме компоновки данных — для проверок пакетного запроса.

Текст запроса в модуле собирается из строковых литералов (в том числе многострочных, с `|`)
всего модуля подряд: временные таблицы живут в менеджере временных таблиц и переходят из
запроса в запрос, а части пакета часто собираются из функций, возвращающих фрагменты текста.
В схеме компоновки — содержимое элементов `<query>`. Номер строки каждого символа сохраняется,
чтобы находка указывала на строку файла."""
from __future__ import annotations

import html
import re
from bisect import bisect_right
from dataclasses import dataclass

from .bsl_line import LineParts

_COMMENT = re.compile(r"//.*")
_PUT = re.compile(r"(?<![\w.])(?:ПОМЕСТИТЬ|INTO)\s+([\w]+)", re.IGNORECASE)
_INDEX = re.compile(r"(?<![\w.])(?:ИНДЕКСИРОВАТЬ\s+ПО|INDEX\s+BY)(?!\w)", re.IGNORECASE)
_DROP = re.compile(r"(?<![\w.])(?:УНИЧТОЖИТЬ|DROP)\s+([\w]+)", re.IGNORECASE)
_JOIN = re.compile(r"(?<![\w.])(?:СОЕДИНЕНИЕ|JOIN)\s+([\w]+)(?![\w.])", re.IGNORECASE)
_SELECT_TOP = re.compile(r"(?<![\w.])(?:ВЫБРАТЬ|SELECT)\s+(?:(?:РАЗЛИЧНЫЕ|DISTINCT|РАЗРЕШЕННЫЕ|ALLOWED)\s+)*"
                         r"(ПЕРВЫЕ|TOP)?", re.IGNORECASE)
_FROM = re.compile(r"(?<![\w.])(?:ИЗ|FROM)(?!\w)", re.IGNORECASE)
_QUERY_OPEN = re.compile(r"<query>", re.IGNORECASE)
_QUERY_CLOSE = re.compile(r"</query>", re.IGNORECASE)


@dataclass(frozen=True)
class QueryStream:
    """Склеенный текст запросов и номер строки файла для каждого его фрагмента."""
    text: str
    starts: tuple[int, ...]       # смещение начала фрагмента в text
    lines: tuple[int, ...]        # номер строки файла фрагмента

    def line_at(self, pos: int) -> int:
        return self.lines[bisect_right(self.starts, pos) - 1]


def _stream(chunks: list[tuple[int, str]]) -> QueryStream:
    parts, starts, lines, pos = [], [], [], 0
    for n, chunk in chunks:
        starts.append(pos)
        lines.append(n)
        parts.append(chunk)
        pos += len(chunk) + 1
    return QueryStream("\n".join(parts), tuple(starts), tuple(lines))


def module_stream(parts: list[LineParts]) -> QueryStream:
    """Литералы модуля подряд (комментарии запроса `//` — отброшены)."""
    return _stream([(n, _COMMENT.sub("", p.literal)) for n, p in enumerate(parts, 1)
                    if not p.comment and p.literal])


def dcs_stream(lines: list[str]) -> QueryStream:
    """Тексты запросов наборов данных схемы компоновки (`<query>…</query>`), без XML-экранирования."""
    chunks: list[tuple[int, str]] = []
    inside = False
    for n, raw in enumerate(lines, 1):
        line = raw
        if not inside:
            m = _QUERY_OPEN.search(line)
            if m is None:
                continue
            inside, line = True, line[m.end():]
        m = _QUERY_CLOSE.search(line)
        if m is not None:
            inside, line = False, line[:m.start()]
        chunks.append((n, _COMMENT.sub("", html.unescape(line))))
    return _stream(chunks)


def is_dcs(path: str, text: str | None = None) -> bool:
    """Файл схемы компоновки данных: `Template.dcs` (EDT) или `Ext/Template.xml` со схемой
    (конфигуратор; макеты других видов лежат так же — смотрим содержимое)."""
    p = path.casefold()
    if p.endswith(".dcs"):
        return True
    return p.endswith("/ext/template.xml") and text is not None and "DataCompositionSchema" in text


@dataclass(frozen=True)
class UnindexedJoin:
    name: str
    put_line: int                 # строка ПОМЕСТИТЬ
    join_lines: tuple[int, ...]   # строки, где таблица стоит после СОЕДИНЕНИЕ


def _small(text: str, put: int, after_put: int, end: int) -> bool:
    start = text.rfind(";", 0, put) + 1
    select = None
    for select in _SELECT_TOP.finditer(text, start, put):
        pass                                     # ближайший к ПОМЕСТИТЬ ВЫБРАТЬ — запрос помещения
    if select is not None and select.group(1):
        return True
    return _FROM.search(text, after_put, end) is None


def unindexed_joined_temp_tables(stream: QueryStream) -> list[UnindexedJoin]:
    """Временные таблицы из `ПОМЕСТИТЬ` без `ИНДЕКСИРОВАТЬ ПО`, которые дальше стоят в соединении
    (`… СОЕДИНЕНИЕ Вт КАК …`). Запрос помещения — от `ПОМЕСТИТЬ` до `;` или следующего `ПОМЕСТИТЬ`;
    таблица живёт до `УНИЧТОЖИТЬ` или следующего помещения с тем же именем. Заведомо маленькая
    таблица — `ВЫБРАТЬ ПЕРВЫЕ n` или выборка без `ИЗ` (одна строка из параметров) — не находка."""
    text = stream.text
    puts = list(_PUT.finditer(text))
    out: list[UnindexedJoin] = []
    for i, put in enumerate(puts):
        name = put.group(1).casefold()
        end = text.find(";", put.end())
        end = len(text) if end == -1 else end
        if i + 1 < len(puts):
            end = min(end, puts[i + 1].start())
        if _INDEX.search(text, put.end(), end) or _small(text, put.start(), put.end(), end):
            continue
        life = len(text)
        for later in puts[i + 1:]:
            if later.group(1).casefold() == name:
                life = later.start()
                break
        for drop in _DROP.finditer(text, put.end(), life):
            if drop.group(1).casefold() == name:
                life = drop.start()
                break
        joins = tuple(stream.line_at(j.start(1)) for j in _JOIN.finditer(text, end, life)
                      if j.group(1).casefold() == name)
        if joins:
            out.append(UnindexedJoin(put.group(1), stream.line_at(put.start()), joins))
    return out
