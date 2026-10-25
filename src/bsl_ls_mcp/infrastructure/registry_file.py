"""Чтение внешнего реестра правил проекта (JSON). Реестр в инструменте не хранится:
путь даёт вызывающий или настройка сервера `BSL_RULES`."""
from __future__ import annotations

import json
from pathlib import Path

from ..domain.ports import SourceError


def load_registry(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise SourceError(f"реестр правил не найден: {p}")
    try:
        data = json.loads(p.read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceError(f"реестр правил {p} не читается как JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SourceError(f"реестр правил {p}: ожидался объект верхнего уровня")
    return data
