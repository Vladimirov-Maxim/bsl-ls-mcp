"""Composition root — единственное место, которое знает про infrastructure.
Собирает граф объектов и отдаёт его через порты. Application импортирует это,
а не конкретные адаптеры → стрелки зависимостей смотрят внутрь к domain.
(Composition root — сборка графа объектов, аналог DI-контейнера.)"""
from __future__ import annotations

from .domain.ports import CodeAnalyzer, LspServer
from .infrastructure.analyze_cli import AnalyzeCliRunner
from .infrastructure.stdio_lsp_client import StdioLspClient
from .settings import Settings


def build_lsp(settings: Settings) -> LspServer:
    """Создать языковой сервис (навигация по тёплому индексу). Возвращает ПОРТ."""
    return StdioLspClient(settings)


def build_analyzer(settings: Settings) -> CodeAnalyzer:
    """Создать анализатор кода (диагностики через analyze-CLI, без индекса)."""
    return AnalyzeCliRunner(settings)
