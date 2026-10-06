"""Шаблонные правила: регулярка проекта + область строки, к которой она применяется.

Области (`где`): Code — код вне литералов и комментариев; Decl — объявление метода
(код строки, включая продолжение литерала); Raw — строка целиком, кроме строки-
комментария; Line — строка целиком; Comment — только строка-комментарий; Query —
текст литералов строки; QueryCont — продолжение многострочного литерала (`|`);
Cond — условие с двумя союзами И/Или на верхнем уровне; Trailing — хвостовые пробелы.
Cond и Trailing встроены в инструмент и шаблона не требуют.

Сравнение с учётом регистра (как у проекта: «НЕ» и «Не» — разные вещи); где нужно
без него — `(?i:…)` в самой регулярке."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .bsl_line import WORD, LineParts, split_line, top_level_conjunctions

AREAS = ("Code", "Decl", "Raw", "Line", "Comment", "Query", "QueryCont", "Cond", "Trailing")
BUILT_IN_AREAS = ("Cond", "Trailing")   # область — сама проверка, шаблон не нужен

_COND_START = re.compile(rf"^\s*(?:Если|ИначеЕсли|Пока|И|Или)(?!{WORD})", re.IGNORECASE)
_TRAILING = re.compile(r"\S[ \t]+$")
GLOBAL_NAMES_PLACEHOLDER = "{ИменаГлобальногоКонтекста}"


@dataclass(frozen=True)
class TemplateRule:
    rule_id: str
    level: str
    regulation: str | None
    text: str
    area: str
    pattern: re.Pattern | None        # None — встроенная область (Cond, Trailing)
    files: re.Pattern | None          # фильтр по пути модуля; None — все модули
    hit_examples: tuple[str, ...] = ()
    miss_examples: tuple[str, ...] = ()

    def applies_to(self, path: str) -> bool:
        return self.files is None or bool(self.files.search(path))

    def hits(self, parts: LineParts, raw: str) -> bool:
        a, p = self.area, self.pattern
        if a == "Code":
            return not parts.comment and not parts.continuation and bool(p.search(parts.code))
        if a == "Decl":
            return not parts.comment and bool(p.search(parts.code))
        if a == "Raw":
            return not parts.comment and bool(p.search(raw))
        if a == "Line":
            return bool(p.search(raw))
        if a == "Comment":
            return parts.comment and bool(p.search(raw))
        if a == "Query":
            return not parts.comment and bool(p.search(parts.literal))
        if a == "QueryCont":
            return parts.continuation and bool(p.search(parts.literal))
        if a == "Cond":
            return (not parts.comment and not parts.continuation
                    and bool(_COND_START.match(parts.code)) and top_level_conjunctions(parts.code) >= 1)
        if a == "Trailing":
            return not parts.comment and bool(_TRAILING.search(raw))
        return False

    def example_hits(self, example: str) -> bool:
        """Срабатывает ли правило на примере (пример бывает многострочным)."""
        for raw in example.replace("\r\n", "\n").split("\n"):
            if raw.strip() and self.hits(split_line(raw), raw):
                return True
        return False


def to_python_regex(pattern: str) -> str:
    """Реестр пишется в совместимом подмножестве .NET/Python; именованные группы .NET
    `(?<имя>…)` и `\\k<имя>` переводятся в синтаксис Python."""
    pattern = re.sub(r"\(\?<(?![=!])(\w+)>", r"(?P<\1>", pattern)
    return re.sub(r"\\k<(\w+)>", r"(?P=\1)", pattern)


def compile_template(pattern: str, global_names: tuple[str, ...]) -> re.Pattern:
    """Шаблон → регулярка: подстановка имён глобального контекста, перевод синтаксиса.
    Ошибка компиляции — исключение re.error (вызывающий превращает её в пропуск правила)."""
    text = pattern.replace(GLOBAL_NAMES_PLACEHOLDER, "|".join(global_names))
    return re.compile(to_python_regex(text))
