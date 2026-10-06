"""Каталог проверок кода — правила, которым мало одной строки: нужен метод, цикл,
блок Попытка или соседний модуль.

У каждой проверки свой постоянный КЛЮЧ (`loop.db-read-via-call`). Номер правила,
уровень, текст и пункт регламента — данные проекта: реестр ссылается на ключ полем
`реализация`, и проект может перенумеровать или переписать правило, не трогая
инструмент. Без реестра проверки работают с умолчаниями отсюда (ид = ключ).

Проверка получает контекст файла (модель модуля, маску строк задачи, знание
платформы, справку о соседних модулях) и возвращает попадания: строка + конкретика.
Нарушение сообщается только на строке задачи (или в методе, которого задача касается),
вендорский код рядом не вменяется."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from functools import cached_property

from .bsl_module import Method, ModuleModel
from .knowledge import MAX_PARAMS, Knowledge

_W = r"[\wА-Яа-яЁё]"
_NOT_BEFORE = r"(?<![\wА-Яа-яЁё.])"


@dataclass(frozen=True)
class Hit:
    line: int
    detail: str = ""


@dataclass
class FileContext:
    path: str                                   # путь от корня выгрузки, прямые косые
    model: ModuleModel
    mask: frozenset[int]                        # строки новой версии, добавленные/изменённые задачей
    knowledge: Knowledge
    own_modules: frozenset[str] = frozenset()   # общие модули, созданные задачей (casefold)
    common_module: Callable[[str], ModuleModel | None] = lambda name: None
    params: dict = field(default_factory=dict)  # параметры правила из реестра

    @cached_property
    def touched(self) -> list[Method]:
        """Методы, которых касается задача (хотя бы одна строка метода в маске)."""
        return [m for m in self.model.methods
                if any(n in self.mask for n in range(m.start, m.end + 1))]

    @cached_property
    def is_object_module(self) -> bool:
        return bool(re.search(r"(ObjectModule|RecordSetModule)\.bsl$", self.path, re.IGNORECASE))

    # --- справка о вызовах внутри модуля: кто читает/пишет базу (транзитивно) ---
    @cached_property
    def call_profile(self) -> CallProfile:
        return _call_profile(self.model, self.knowledge)

    @cached_property
    def tx_hits(self) -> dict[str, list[Hit]]:
        return _transaction_hits(self)


@dataclass(frozen=True)
class CodeCheck:
    key: str
    level: str          # уровень по умолчанию (без реестра)
    text: str           # текст по умолчанию (без реестра), нейтральный
    run: Callable[[FileContext], Iterable[Hit]]


# ---------------------------------------------------------------------------
# выходной параметр без инициализации

_PROPERTY_OUT = re.compile(r'Свойство\s*\(\s*"[^"]*"\s*,\s*([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*)\s*\)')


def _out_param_uninitialized(ctx: FileContext) -> Iterator[Hit]:
    """`Структура.Свойство("Ключ", Переменная)` читается как объявление, не будучи им:
    если выше по модулю нет ни присваивания, ни `Перем`, код не компилируется."""
    lines = ctx.model.lines
    literals = {x.casefold() for x in ctx.knowledge.language_literals}
    for i, line in enumerate(lines):
        if i + 1 not in ctx.mask:
            continue
        m = _PROPERTY_OUT.search(line)
        if not m:
            continue
        name = m.group(1)
        if name.casefold() in literals:
            continue
        esc = re.escape(name)
        declared = re.compile(rf"(^|\s)Перем\s+.*\b{esc}\b", re.IGNORECASE)
        assigned = re.compile(rf"^\s*{esc}\s*=", re.IGNORECASE)
        if not any(declared.search(lines[j]) or assigned.match(lines[j]) for j in range(i)):
            yield Hit(i + 1, f"«{name}»")


# ---------------------------------------------------------------------------
# оформление

def _bare_blank_line(ctx: FileContext) -> Iterator[Hit]:
    """Пустая строка внутри метода без табуляции: конфигуратор хранит в ней отступ по
    уровню вложенности, и первая выгрузка из базы даст ложную правку."""
    for m in ctx.model.methods:
        for n in range(m.start + 1, m.end):
            if n in ctx.mask and ctx.model.lines[n - 1] == "":
                yield Hit(n, m.name)


# ---------------------------------------------------------------------------
# параметры

def _too_many_params(ctx: FileContext) -> Iterator[Hit]:
    limit = int(ctx.params.get("порог", MAX_PARAMS))
    for m in ctx.touched:
        if m.start in ctx.mask and len(m.params) > limit:
            yield Hit(m.start, f"{m.name}: {len(m.params)} при пороге {limit}")


def _optional_before_required(ctx: FileContext) -> Iterator[Hit]:
    for m in ctx.touched:
        if m.start not in ctx.mask:
            continue
        seen_optional = False
        for p in m.params:
            if p.optional:
                seen_optional = True
            elif seen_optional:
                yield Hit(m.start, f"{m.name}: {p.text}")
                break


# ---------------------------------------------------------------------------
# чтение и запись в цикле

def _loop_lines(ctx: FileContext) -> Iterator[tuple[Method, int, str]]:
    """Строки задачи внутри цикла: (метод, строка, код)."""
    for m in ctx.touched:
        body = ctx.model.body(m)
        for n in m.body_lines():
            if n in ctx.mask and body.loop[n] > 0:
                yield m, n, body.code[n]


def _loop_db_read(ctx: FileContext) -> Iterator[Hit]:
    read = ctx.knowledge.regex("db_read")
    for _m, n, code in _loop_lines(ctx):
        if read.search(code):
            yield Hit(n)


def _loop_db_write(ctx: FileContext) -> Iterator[Hit]:
    write = ctx.knowledge.regex("db_write")
    for _m, n, code in _loop_lines(ctx):
        if write.search(code):
            yield Hit(n)


_LOCAL_CALL = re.compile(rf"{_NOT_BEFORE}({_W}+)\s*\(")


@dataclass(frozen=True)
class CallProfile:
    readers: dict[str, str]   # метод (casefold) → как он читает базу («строка 12», «через X, строка 30»)
    writers: dict[str, str]
    units: frozenset[str]     # методы со своей транзакцией — единица обработки, не «чтение в цикле»


def _call_profile(model: ModuleModel, knowledge: Knowledge) -> CallProfile:
    """Признак «читает/пишет базу» транзитивен в пределах модуля: цикл → метод → метод →
    чтение. Межмодульные вызовы не прослеживаются — справка только по этому модулю."""
    read = knowledge.regex("db_read")
    write = knowledge.regex("db_write")
    begin = re.compile(rf"{_NOT_BEFORE}НачатьТранзакцию\s*\(", re.IGNORECASE)
    names = model.method_by_name
    readers: dict[str, str] = {}
    writers: dict[str, str] = {}
    units: set[str] = set()
    calls: dict[str, list[str]] = {}
    for m in model.methods:
        key = m.name.casefold()
        calls[key] = []
        for n in m.body_lines():
            code = model.code(n)
            if begin.search(code):
                units.add(key)
            if key not in readers and read.search(code):
                readers[key] = f"строка {n}"
            if key not in writers and write.search(code):
                writers[key] = f"строка {n}"
            for c in _LOCAL_CALL.finditer(code):
                callee = c.group(1).casefold()
                if callee != key and callee in names and callee not in calls[key]:
                    calls[key].append(callee)
    for found in (readers, writers):
        changed = True
        while changed:
            changed = False
            for m in model.methods:
                key = m.name.casefold()
                if key in found:
                    continue
                for callee in calls[key]:
                    if callee in found:
                        found[key] = f"через {names[callee].name}, {found[callee]}"
                        changed = True
                        break
    return CallProfile(readers=readers, writers=writers, units=frozenset(units))


def _loop_calls(ctx: FileContext) -> Iterator[tuple[int, str]]:
    """Вызовы методов этого же модуля в строках задачи внутри цикла: (строка, имя вызываемого)."""
    names = ctx.model.method_by_name
    for m, n, code in _loop_lines(ctx):
        for c in _LOCAL_CALL.finditer(code):
            callee = c.group(1).casefold()
            if callee == m.name.casefold() or callee not in names:
                continue
            if callee in ctx.call_profile.units:   # своя транзакция — единица обработки по одному объекту
                continue
            yield n, callee


def _loop_db_read_via_call(ctx: FileContext) -> Iterator[Hit]:
    readers = ctx.call_profile.readers
    for n, callee in _loop_calls(ctx):
        if callee in readers:
            yield Hit(n, f"{ctx.model.method_by_name[callee].name} ({readers[callee]})")


def _loop_db_write_via_call(ctx: FileContext) -> Iterator[Hit]:
    writers = ctx.call_profile.writers
    for n, callee in _loop_calls(ctx):
        if callee in writers:
            yield Hit(n, f"{ctx.model.method_by_name[callee].name} ({writers[callee]})")


_ATTRIBUTE_VALUE = re.compile(r"ЗначениеРеквизитаОбъекта\s*\(\s*([^,]+),")


def _repeated_attribute_value(ctx: FileContext) -> Iterator[Hit]:
    """Второй ЗначениеРеквизитаОбъекта по той же ссылке в одном методе — два обращения
    к базе вместо одного ЗначенияРеквизитовОбъекта."""
    for m in ctx.touched:
        body = ctx.model.body(m)
        first: dict[str, int] = {}
        for n in m.body_lines():
            for a in _ATTRIBUTE_VALUE.finditer(body.code[n]):
                ref = a.group(1).strip()
                key = ref.casefold()
                if key in first:
                    if n in ctx.mask or first[key] in ctx.mask:
                        yield Hit(n, f"{ref}, первый вызов — строка {first[key]}")
                else:
                    first[key] = n


_MOVEMENTS_WRITE = re.compile(rf"Движения\.{_W}+\.Записать\s*\(", re.IGNORECASE)


def _explicit_movements_write(ctx: FileContext) -> Iterator[Hit]:
    if not re.search(r"ObjectModule\.bsl$", ctx.path, re.IGNORECASE):
        return
    for m in ctx.touched:
        body = ctx.model.body(m)
        for n in m.body_lines():
            if n in ctx.mask and _MOVEMENTS_WRITE.search(body.code[n]):
                yield Hit(n)


# ---------------------------------------------------------------------------
# форма транзакции

_TX_BEGIN = re.compile(rf"{_NOT_BEFORE}НачатьТранзакцию\s*\(", re.IGNORECASE)
_TX_COMMIT = re.compile(r"ЗафиксироватьТранзакцию\s*\(", re.IGNORECASE)
_TX_ROLLBACK = re.compile(r"ОтменитьТранзакцию\s*\(", re.IGNORECASE)
_LOCK = re.compile(r"\.Заблокировать\s*\(", re.IGNORECASE)
_RERAISE = re.compile(rf"{_NOT_BEFORE}ВызватьИсключение", re.IGNORECASE)
_LOG = re.compile(r"ЗаписьЖурналаРегистрации\s*\(", re.IGNORECASE)
_READ_IN_TX = re.compile(r"\.Выполнить\s*\(\s*\)|\.Прочитать\s*\(\s*\)", re.IGNORECASE)


def _transaction_hits(ctx: FileContext) -> dict[str, list[Hit]]:
    """Все проверки формы транзакции за один проход: эталонная форма —
    НачатьТранзакцию → сразу Попытка (блокировка, чтение, запись, ЗафиксироватьТранзакцию
    последним) → Исключение (ОтменитьТранзакцию первым, журнал, ВызватьИсключение).
    Проверяется каждая НачатьТранзакцию, добавленная задачей."""
    hits: dict[str, list[Hit]] = {}

    def add(key: str, line: int, detail: str = "") -> None:
        hits.setdefault(key, []).append(Hit(line, detail))

    dialog = re.compile(rf"{_NOT_BEFORE}(?:{'|'.join(map(re.escape, ctx.knowledge.dialogs))})\s*\(",
                        re.IGNORECASE)
    handlers = {h.casefold() for h in ctx.knowledge.transactional_handlers}
    for m in ctx.touched:
        body = ctx.model.body(m)
        codes = body.code
        begins = sorted(n for n, c in codes.items() if _TX_BEGIN.search(c))
        for bn in begins:
            if bn not in ctx.mask:
                continue
            if m.name.casefold() in handlers and ctx.is_object_module:
                add("tx.in-write-handler", bn, m.name)
            if not any(_TX_COMMIT.search(c) for c in codes.values()):
                add("tx.no-commit", bn)
            after = sorted((t for t in body.tries if t.try_line > bn), key=lambda t: t.try_line)
            block = after[0] if after else None
            if block is None or block.except_line == 0:
                add("tx.no-try", bn)
                continue
            for n in range(bn + 1, block.try_line):
                if codes.get(n, "").strip():
                    add("tx.lock-outside-try" if _LOCK.search(codes[n]) else "tx.code-before-try", n)
                    break
            in_try = [n for n in range(block.try_line + 1, block.except_line) if codes.get(n, "").strip()]
            in_except = [n for n in range(block.except_line + 1, block.end_line) if codes.get(n, "").strip()]
            if in_try and not _TX_COMMIT.search(codes[in_try[-1]]):
                add("tx.commit-not-last", in_try[-1])
            rollback = next((n for n in in_except if _TX_ROLLBACK.search(codes[n])), None)
            if rollback is None:
                add("tx.no-rollback", block.except_line)
            else:
                if in_except[0] != rollback:
                    add("tx.code-before-rollback", in_except[0])
                if not any(n > rollback and _RERAISE.search(codes[n]) for n in in_except):
                    add("tx.no-reraise", block.except_line)
                if not any(_LOG.search(codes[n]) for n in in_except):
                    add("tx.rollback-not-logged", block.except_line)
            first_read = next((n for n in in_try if _READ_IN_TX.search(codes[n])), None)
            lock = next((n for n in in_try if _LOCK.search(codes[n])), None)
            if first_read and lock and lock > first_read:
                add("tx.lock-after-read", lock, f"чтение — строка {first_read}")
            for n in in_try:
                if dialog.search(codes[n]):
                    add("tx.dialog-inside", n)
    return hits


def _tx(key: str) -> Callable[[FileContext], list[Hit]]:
    return lambda ctx: ctx.tx_hits.get(key, [])


_EXCHANGE_LOAD = re.compile(r"ОбменДанными\.Загрузка", re.IGNORECASE)


def _handler_without_exchange_check(ctx: FileContext) -> Iterator[Hit]:
    """Обработчик записи без `Если ОбменДанными.Загрузка Тогда Возврат` — бизнес-логика
    выполнится и при загрузке данных обмена."""
    if not ctx.is_object_module:
        return
    guarded = {h.casefold() for h in ctx.knowledge.exchange_guarded_handlers}
    for m in ctx.touched:
        if m.start in ctx.mask and m.name.casefold() in guarded:
            body = ctx.model.body(m)
            if not any(_EXCHANGE_LOAD.search(c) for c in body.code.values()):
                yield Hit(m.start, m.name)


# ---------------------------------------------------------------------------
# вызов служебного интерфейса чужого общего модуля

_MODULE_CALL = re.compile(rf"{_NOT_BEFORE}({_W}+)\.({_W}+)\s*\(")


def _foreign_internal_api(ctx: FileContext) -> Iterator[Hit]:
    """Метод из области СлужебныйПрограммныйИнтерфейс чужого общего модуля вызывать
    нельзя: служебный интерфейс меняется без предупреждения. «Свои» — модуль этого же
    файла, общие модули, созданные задачей, и модули с префиксом доработок проекта."""
    own_self = re.match(r"^CommonModules/([^/]+)/", ctx.path, re.IGNORECASE)
    self_name = own_self.group(1).casefold() if own_self else ""
    region = ctx.knowledge.internal_api_region.casefold()
    seen: set[tuple] = set()
    for n in sorted(ctx.mask):
        if n > len(ctx.model.lines):
            continue
        parts = ctx.model.parts[n - 1]
        if parts.comment or parts.continuation:
            continue
        for c in _MODULE_CALL.finditer(parts.code):
            module, method = c.group(1), c.group(2)
            folded = module.casefold()
            if folded == self_name or folded in ctx.own_modules or ctx.knowledge.is_own(module):
                continue
            other = ctx.common_module(module)
            if other is None:
                continue
            key = (n, folded, method.casefold())
            if key in seen:
                continue
            seen.add(key)
            regions = other.method_regions.get(method.casefold())
            if regions is not None and any(r.casefold() == region for r in regions):
                yield Hit(n, f"{module}.{method}")


# ---------------------------------------------------------------------------

CATALOG: dict[str, CodeCheck] = {c.key: c for c in (
    CodeCheck("out-param.uninitialized", "Fail",
              "переменная впервые появляется выходным параметром, присваивания выше нет",
              _out_param_uninitialized),
    CodeCheck("layout.bare-blank-line", "Fail",
              "пустая строка внутри метода без табуляции", _bare_blank_line),
    CodeCheck("params.too-many", "Warn", "у метода слишком много параметров", _too_many_params),
    CodeCheck("params.optional-before-required", "Fail",
              "обязательный параметр после необязательного", _optional_before_required),
    CodeCheck("loop.db-read", "Fail", "чтение из базы внутри цикла", _loop_db_read),
    CodeCheck("loop.db-read-via-call", "Warn",
              "в цикле вызывается метод, который читает базу", _loop_db_read_via_call),
    CodeCheck("loop.db-write-via-call", "Warn",
              "в цикле вызывается метод, который пишет в базу", _loop_db_write_via_call),
    CodeCheck("loop.db-write", "Warn", "запись в базу в цикле по одной", _loop_db_write),
    CodeCheck("read.repeated-attribute-value", "Fail",
              "второй ЗначениеРеквизитаОбъекта по той же ссылке", _repeated_attribute_value),
    CodeCheck("document.explicit-movements-write", "Warn",
              "явная запись движений в модуле объекта", _explicit_movements_write),
    CodeCheck("tx.in-write-handler", "Fail",
              "своя транзакция в обработчике записи — платформа уже открыла транзакцию",
              _tx("tx.in-write-handler")),
    CodeCheck("tx.no-commit", "Fail", "НачатьТранзакцию без ЗафиксироватьТранзакцию в том же методе",
              _tx("tx.no-commit")),
    CodeCheck("tx.no-try", "Fail", "после НачатьТранзакцию нет блока Попытка … Исключение",
              _tx("tx.no-try")),
    CodeCheck("tx.lock-outside-try", "Fail", "блокировка установлена вне Попытка",
              _tx("tx.lock-outside-try")),
    CodeCheck("tx.code-before-try", "Fail", "код между НачатьТранзакцию и Попытка",
              _tx("tx.code-before-try")),
    CodeCheck("tx.commit-not-last", "Fail",
              "последним оператором Попытка должна быть ЗафиксироватьТранзакцию",
              _tx("tx.commit-not-last")),
    CodeCheck("tx.no-rollback", "Fail", "в Исключение нет ОтменитьТранзакцию",
              _tx("tx.no-rollback")),
    CodeCheck("tx.code-before-rollback", "Fail", "до ОтменитьТранзакцию в Исключение выполняется код",
              _tx("tx.code-before-rollback")),
    CodeCheck("tx.no-reraise", "Fail", "после ОтменитьТранзакцию нет ВызватьИсключение",
              _tx("tx.no-reraise")),
    CodeCheck("tx.rollback-not-logged", "Warn", "откат транзакции не записывается в журнал регистрации",
              _tx("tx.rollback-not-logged")),
    CodeCheck("tx.lock-after-read", "Fail", "блокировка после чтения", _tx("tx.lock-after-read")),
    CodeCheck("tx.dialog-inside", "Fail", "диалог с пользователем внутри транзакции",
              _tx("tx.dialog-inside")),
    CodeCheck("handler.no-data-exchange-check", "Fail",
              "обработчик записи без проверки ОбменДанными.Загрузка", _handler_without_exchange_check),
    CodeCheck("call.foreign-internal-api", "Fail",
              "вызов метода из области СлужебныйПрограммныйИнтерфейс чужого общего модуля",
              _foreign_internal_api),
)}
