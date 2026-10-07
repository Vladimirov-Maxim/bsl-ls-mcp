"""Composition root — единственное место, которое знает про infrastructure.
Собирает граф объектов и отдаёт его через порты. Application импортирует это,
а не конкретные адаптеры → стрелки зависимостей смотрят внутрь к domain.
(Composition root — сборка графа объектов, аналог DI-контейнера.)"""
from __future__ import annotations

from pathlib import Path

from .domain.ports import ChangeSource, CodeAnalyzer, LspServer
from .infrastructure.analyze_cli import AnalyzeCliRunner
from .infrastructure.dir_changes import DirPairChangeSource
from .infrastructure.git_changes import GitChangeSource
from .infrastructure.registry_file import load_registry
from .infrastructure.stdio_lsp_client import StdioLspClient
from .settings import Settings


def build_change_source(settings: Settings, *, repo: str | None = None, base: str | None = None,
                        rev: str | None = None, baseline: str | None = None,
                        target: str | None = None) -> ChangeSource:
    """Источник правок задачи: git-репозиторий исходников или пара каталогов.
    base не задан — точка ответвления от ветки разработки (BSL_BASE_BRANCH), иначе HEAD."""
    if repo:
        return GitChangeSource(repo, base or None, rev, git=settings.git_path,
                               base_branch=settings.base_branch)
    return DirPairChangeSource(baseline, target)


def load_rules(settings: Settings, path: str | Path | None) -> dict | None:
    """Реестр правил проекта: явный путь, иначе BSL_RULES; без них — встроенный каталог."""
    chosen = path or settings.rules_path
    return load_registry(chosen) if chosen else None


def build_lsp(settings: Settings) -> LspServer:
    """Создать языковой сервис (навигация по тёплому индексу). Возвращает ПОРТ."""
    return StdioLspClient(settings)


def build_analyzer(settings: Settings) -> CodeAnalyzer:
    """Создать анализатор кода (диагностики через analyze-CLI, без индекса)."""
    return AnalyzeCliRunner(settings)
