"""Доменные модели ответов. Форма — русские имена 1С (как в ПолноеИмя())."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolRef:
    """Узел навигационного ответа в именах 1С (а не сырой LSP)."""
    name: str            # имя символа, напр. "ВычислитьСуммуПоДоговору"
    type: str            # тип объекта 1С (рус.), напр. "ОбщийМодуль", "Документ"
    full_name: str       # полное имя, напр. "ОбщийМодуль.МойМодуль.ИмяФункции"
    kind: str            # вид LSP-символа: "function" | "procedure" | ...


@dataclass(frozen=True)
class Diagnostic:
    """Одна диагностика по файлу."""
    file: str            # полное имя/путь объекта
    line: int            # 1-based строка
    code: str            # ключ диагностики BSL LS, напр. "MissingSpace"
    severity: str        # "error" | "warning" | "info" | "hint"
    message: str


@dataclass(frozen=True)
class CodeLocation:
    """Место в коде (для definition/references) в именах 1С + контекст строки."""
    type: str            # тип объекта 1С (рус.), напр. "ОбщийМодуль"
    module: str          # имя модуля/объекта, напр. "МойМодуль"
    full_name: str       # полное имя модуля, напр. "ОбщийМодуль.МойМодуль"
    line: int            # 1-based строка
    text: str            # код строки (контекст использования)


@dataclass(frozen=True)
class Complexity:
    full_name: str
    cognitive: int | None    # None — метрика не измерена (линза отключена в конфиге)
    cyclomatic: int | None
