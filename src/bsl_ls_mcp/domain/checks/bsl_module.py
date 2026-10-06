"""Модель модуля BSL: методы (имя, вид, экспорт, параметры, границы), циклы и блоки
Попытка внутри метода, области верхнего уровня. Разбор — по тексту, без LSP: так
проверяется любое дерево (рабочая копия, ревизия, пара каталогов), а не только то,
по которому построен индекс."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property

from .bsl_line import WORD, LineParts, split_line

_DECL = re.compile(rf"^\s*(Асинх\s+)?(Процедура|Функция)\s+({WORD}+)\s*\((.*)$", re.IGNORECASE)
_END = re.compile(rf"^\s*(?:КонецПроцедуры|КонецФункции)(?!{WORD})", re.IGNORECASE)
_EXPORT = re.compile(rf"\)\s*Экспорт(?!{WORD})", re.IGNORECASE)
_BLOCK_TOKEN = re.compile(
    rf"(?<![\wА-Яа-яЁё.])(Для|Пока|КонецЦикла|Попытка|Исключение|КонецПопытки)(?!{WORD})", re.IGNORECASE)
_LOOP_OPEN = re.compile(rf"(?<![\wА-Яа-яЁё.])(?:Для|Пока)(?!{WORD})", re.IGNORECASE)
_LOOP_CLOSE = re.compile(r"КонецЦикла", re.IGNORECASE)
_REGION_OPEN = re.compile(rf"^\s*#Область\s+({WORD}+)", re.IGNORECASE)
_REGION_CLOSE = re.compile(r"^\s*#КонецОбласти", re.IGNORECASE)
_RAW_DECL = re.compile(rf"^\s*(?:Асинх\s+)?(?:Процедура|Функция)\s+({WORD}+)\s*\(", re.IGNORECASE)


@dataclass(frozen=True)
class Param:
    text: str

    @property
    def optional(self) -> bool:
        return "=" in self.text


@dataclass(frozen=True)
class Method:
    name: str
    kind: str          # "Процедура" | "Функция" (как в тексте модуля, с канонической капитализацией)
    is_async: bool
    export: bool
    start: int         # строка объявления (1-based)
    decl_end: int      # последняя строка объявления (параметры бывают перенесены)
    end: int           # строка КонецПроцедуры/КонецФункции
    params_text: str   # текст между «(» и первой «)»

    @property
    def params(self) -> tuple[Param, ...]:
        return tuple(Param(p) for p in split_params(self.params_text))

    def body_lines(self) -> range:
        """Строки тела: от строки после объявления до строки перед концом метода."""
        return range(self.decl_end + 1, self.end)


@dataclass(frozen=True)
class TryBlock:
    try_line: int
    except_line: int   # 0 — блок без Исключение
    end_line: int


@dataclass
class MethodBody:
    loop: dict[int, int] = field(default_factory=dict)   # строка → глубина циклов
    code: dict[int, str] = field(default_factory=dict)   # строка → код без литералов/комментариев
    tries: list[TryBlock] = field(default_factory=list)  # закрытые блоки Попытка


def split_params(text: str) -> list[str]:
    """Параметры объявления через запятую — с учётом вложенных скобок значений по умолчанию."""
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if "".join(buf).strip():
        out.append("".join(buf))
    return [p.strip() for p in out if p.strip()]


class ModuleModel:
    """Модуль, разобранный один раз: строки, части строк, методы, области."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self._bodies: dict[int, MethodBody] = {}

    @cached_property
    def parts(self) -> list[LineParts]:
        return [split_line(line) for line in self.lines]

    def code(self, n: int) -> str:
        """Код строки n (1-based)."""
        return self.parts[n - 1].code

    @cached_property
    def methods(self) -> list[Method]:
        methods: list[Method] = []
        cur: dict | None = None
        count = len(self.lines)
        for i in range(count):
            code = self.parts[i].code
            if cur is None:
                m = _DECL.match(code)
                if m:
                    params = m.group(4)
                    j = i
                    while ")" not in params and j + 1 < count:
                        j += 1
                        params += " " + self.parts[j].code
                    decl_full = " ".join(self.parts[k].code for k in range(i, j + 1))
                    kind = "Функция" if m.group(2).casefold() == "функция" else "Процедура"
                    cur = {"name": m.group(3), "kind": kind, "is_async": bool(m.group(1)),
                           "export": bool(_EXPORT.search(decl_full)), "start": i + 1,
                           "decl_end": j + 1, "params_text": re.sub(r"\).*$", "", params)}
                    continue
            if cur is not None and _END.match(code):
                methods.append(Method(end=i + 1, **cur))
                cur = None
        return methods

    @cached_property
    def method_by_name(self) -> dict[str, Method]:
        """Имя метода (без учёта регистра, как в 1С) → метод; при повторе — последний."""
        return {m.name.casefold(): m for m in self.methods}

    def method_at(self, n: int) -> Method | None:
        for m in self.methods:
            if m.start <= n <= m.end:
                return m
        return None

    def body(self, method: Method) -> MethodBody:
        if method.start not in self._bodies:
            self._bodies[method.start] = _build_body(self, method)
        return self._bodies[method.start]

    @cached_property
    def method_regions(self) -> dict[str, tuple[str, ...]]:
        """Имя метода (casefold) → стек областей, в которых он объявлен (по сырому тексту)."""
        regions: dict[str, tuple[str, ...]] = {}
        stack: list[str] = []
        for line in self.lines:
            m = _REGION_OPEN.match(line)
            if m:
                stack.append(m.group(1))
                continue
            if _REGION_CLOSE.match(line):
                if stack:
                    stack.pop()
                continue
            m = _RAW_DECL.match(line)
            if m:
                regions[m.group(1).casefold()] = tuple(stack)
        return regions


def _build_body(model: ModuleModel, method: Method) -> MethodBody:
    """Глубина циклов и блоки Попытка по строкам тела метода. Строка, открывающая цикл,
    сама ещё не «в цикле»; строка КонецЦикла — ещё в цикле."""
    body = MethodBody()
    depth = 0
    open_tries: list[list[int]] = []   # [try, except]
    for n in method.body_lines():
        code = model.code(n)
        body.code[n] = code
        body.loop[n] = depth
        for token in _BLOCK_TOKEN.finditer(code):
            word = token.group(1).casefold()
            if word in ("для", "пока"):
                depth += 1
            elif word == "конеццикла":
                if depth > 0:
                    depth -= 1
            elif word == "попытка":
                open_tries.append([n, 0])
            elif word == "исключение":
                if open_tries:
                    open_tries[-1][1] = n
            elif word == "конецпопытки":
                if open_tries:
                    t, e = open_tries.pop()
                    body.tries.append(TryBlock(try_line=t, except_line=e, end_line=n))
        if _LOOP_OPEN.search(code) and not _LOOP_CLOSE.search(code):
            body.loop[n] = depth - 1
    return body
