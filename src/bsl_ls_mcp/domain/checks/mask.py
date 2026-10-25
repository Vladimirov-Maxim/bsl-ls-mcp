"""Маска строк задачи: номера строк НОВОЙ версии файла, которые задача добавила или
изменила. Правило видит только то, что пересекается с маской: вендорский код рядом
задаче не вменяется."""
from __future__ import annotations

import difflib
import re

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def mask_from_unified_diff(diff: str) -> frozenset[int]:
    """Строки, добавленные в новой версии, по выводу `git diff -U0` одного файла."""
    added: set[int] = set()
    n = 0
    for raw in diff.split("\n"):
        line = raw.rstrip("\r")
        m = _HUNK.match(line)
        if m:
            n = int(m.group(1))
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.add(n)
            n += 1
        elif line.startswith(" "):
            n += 1
    return frozenset(added)


def mask_from_texts(old: list[str], new: list[str]) -> frozenset[int]:
    """Строки новой версии, которых нет в старой (вставка или замена) — для пары
    каталогов, где git нет. Сравнение построчное, как у обычного diff."""
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    added: set[int] = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            added.update(range(j1 + 1, j2 + 1))
    return frozenset(added)


def whole_file(lines: list[str]) -> frozenset[int]:
    """Новый файл задачи проверяется целиком."""
    return frozenset(range(1, len(lines) + 1))
