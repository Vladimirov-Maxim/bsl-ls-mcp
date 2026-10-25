"""Разбор одной строки BSL: код вне литералов и комментариев, текст литералов,
признаки «комментарий» и «продолжение многострочного литерала» (строка с `|`)."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Буква/цифра/подчёркивание в идентификаторе 1С. `\w` в Python уже юникодный;
# кириллица перечислена явно для читаемости шаблонов.
WORD = r"[\wА-Яа-яЁё]"


@dataclass(frozen=True)
class LineParts:
    code: str            # код без литералов (каждый закрытый литерал заменён на "") и без комментария
    literal: str         # склеенный текст строковых литералов строки
    comment: bool        # строка целиком — комментарий (`//` первым непробельным)
    continuation: bool   # строка продолжает многострочный литерал (`|` первым непробельным)


def split_line(line: str) -> LineParts:
    trimmed = line.lstrip()
    in_string = False
    start = 0
    if trimmed.startswith("|"):
        in_string = True
        start = len(line) - len(trimmed) + 1
    elif trimmed.startswith("//"):
        return LineParts(code="", literal="", comment=True, continuation=False)
    code: list[str] = []
    literal: list[str] = []
    i = start
    n = len(line)
    while i < n:
        ch = line[i]
        if in_string:
            if ch == '"':
                if i + 1 < n and line[i + 1] == '"':
                    literal.append('"')
                    i += 2
                    continue
                in_string = False
                code.append('""')
                i += 1
                continue
            literal.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            break
        code.append(ch)
        i += 1
    return LineParts(code="".join(code), literal="".join(literal), comment=False,
                     continuation=trimmed.startswith("|"))


_LEADING_KEYWORD = re.compile(r"^\s*(?:ИначеЕсли|Если|Пока|И|Или)\s+", re.IGNORECASE)
_CONJUNCTION = re.compile(rf"(?<!{WORD})(?:И|Или)(?!{WORD})", re.IGNORECASE)


def top_level_conjunctions(code: str) -> int:
    """Сколько союзов И/Или стоит на нулевой глубине скобок после ведущего ключевого
    слова строки (`Если`, `ИначеЕсли`, `Пока`, `И`, `Или`)."""
    depth = 0
    flat: list[str] = []
    for ch in code:
        if ch == "(":
            depth += 1
            flat.append(" ")
            continue
        if ch == ")":
            if depth > 0:
                depth -= 1
            flat.append(" ")
            continue
        flat.append(ch if depth == 0 else " ")
    body = _LEADING_KEYWORD.sub(" ", "".join(flat), count=1)
    return len(_CONJUNCTION.findall(body))


def split_text(text: str) -> list[str]:
    """Текст файла → строки без переводов строк; последний перевод строки не даёт
    лишней пустой строки (как ReadAllLines)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines
