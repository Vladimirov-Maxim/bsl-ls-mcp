"""Порт языкового сервиса. Единственный шов, который реально стоит изолировать:
волатильная часть (вендор/версия/транспорт). Application зависит от этого
Protocol, а не от конкретного stdio-клиента → подменяемо в тестах.

Намеренно НЕ ABC, а Protocol (duck typing) — неформальные порты.
Методы оперируют LSP-позициями; перевод имя↔позиция и форму ответа держат
resolver/mapper, не порт."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


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
