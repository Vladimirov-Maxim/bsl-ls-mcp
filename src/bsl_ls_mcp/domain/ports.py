"""Порт языкового сервиса. Единственный шов, который реально стоит изолировать:
волатильная часть (вендор/версия/транспорт). Application зависит от этого
Protocol, а не от конкретного stdio-клиента → подменяемо в тестах.

Намеренно НЕ ABC, а Protocol (duck typing) — неформальные порты.
Методы оперируют LSP-позициями; перевод имя↔позиция и форму ответа держат
resolver/mapper, не порт."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class SourceError(Exception):
    """Источник правок не открылся: нет репозитория/каталога, неверная база, чужой формат
    выгрузки. Текст — для человека, его показывают как есть."""


@dataclass(frozen=True)
class Change:
    status: str    # "A" | "M" | "D" (переименование — новый путь со статусом "R")
    path: str      # от корня выгрузки, прямые косые
    old_path: str | None = None   # прежний путь переименованного/скопированного файла


class ChangeSource(Protocol):
    """Источник правок задачи для проверок кода: git (база ↔ рабочая копия или ревизия)
    или пара каталогов эталон ↔ копия. Только чтение."""

    mode: str      # "git" | "каталоги"
    config_prefixes: tuple[str, ...]   # корни конфигурации от корня источника: '' или 'App/src/'

    def changes(self) -> list[Change]:
        """Состав правок: добавленные, изменённые, удалённые файлы."""
        ...

    def changed_lines(self, change: Change) -> frozenset[int]:
        """Строки новой версии файла, добавленные или изменённые задачей; новый файл — все."""
        ...

    def new_text(self, path: str) -> str | None:
        """Текст новой версии файла (None — файла нет)."""
        ...

    def old_text(self, change: Change) -> str | None:
        """Текст базовой версии файла (None — в базе файла не было)."""
        ...


class TextSearch(Protocol):
    """Текстовый поиск по .bsl workspace — кандидаты для вызовов по строковому имени."""

    def files_with(self, fragment: str) -> list[tuple[Path, str]]:
        """(путь, текст) файлов, содержащих fragment (регистр учитывается)."""
        ...


class CodeAnalyzer(Protocol):
    """Проверка кода (диагностики) — пофайловый статический анализ, БЕЗ индекса.
    Отдельный шов от LspServer: навигации нужен тёплый граф, проверке — нет.
    Реализация спавнит разовый `bsl-language-server analyze` (см. infrastructure)."""

    async def analyze(self, src_dir: Path) -> list[dict[str, Any]]:
        """Прогнать analyze по каталогу/модулю → сырые диагностики (форма analyze-JSON)."""
        ...


class LspServer(Protocol):
    async def start(self) -> None:
        """Поднять java-процесс BSL LS (stdio), initialize(rootUri), дождаться индекса."""
        ...

    async def stop(self) -> None:
        """Корректно погасить сервер (shutdown/exit)."""
        ...

    def kill(self) -> None:
        """Синхронно прибить сервер (для atexit при остановке демона)."""
        ...

    def is_ready(self) -> bool:
        """Индекс построен и сервер жив — без ожидания (выбор источника диагностик)."""
        ...

    def is_new(self, uri: str) -> bool:
        """Файл создан после индексации — контекст метаданных его объекта неполон до реиндекса."""
        ...

    async def close(self, uri: str, forget: bool = False) -> None:
        """Закрыть документ; forget — убрать его и из контекста сервера (файл вне workspace,
        открытый на время одного запроса, не должен остаться в графе вызовов)."""
        ...

    def warm_up(self) -> None:
        """Запустить сервер и индексацию в фоне, не дожидаясь готовности."""
        ...

    async def restart(self) -> None:
        """Полный реиндекс in-place (погасить и поднять сервер заново)."""
        ...

    async def diagnostics(self, uri: str) -> list[dict[str, Any]]:
        """Диагностики по файлу (синхронизирует с диском, ждёт publishDiagnostics)."""
        ...

    async def prepare_call_hierarchy(self, uri: str, line: int, character: int) -> list[dict]:
        ...

    async def incoming_calls(self, item: dict) -> list[dict]:
        ...

    async def outgoing_calls(self, item: dict) -> list[dict]:
        ...

    async def definition(self, uri: str, line: int, character: int) -> list[dict]:
        ...

    async def references(self, uri: str, line: int, character: int) -> list[dict]:
        ...

    async def document_symbol(self, uri: str) -> list[dict]:
        """Символ-дерево документа (методы с точными позициями из AST сервера)."""
        ...

    async def code_lens(self, uri: str) -> list[dict]:
        """Code lenses документа (для сложности — без чисел, нужен resolve)."""
        ...

    async def resolve_code_lens(self, lens: dict) -> dict:
        """Резолв линзы → command.title с числом."""
        ...
