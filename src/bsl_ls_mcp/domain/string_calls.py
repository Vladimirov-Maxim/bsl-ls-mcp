"""Вызов метода по строковому имени: `ДлительныеОперации.ВыполнитьФункцию(…, "Модуль.Метод")`,
`ОбщегоНазначения.ВыполнитьМетодКонфигурации("Модуль.Метод", …)`, имя в переменной
`ВыполняемыйМетод = "Модуль.Метод"` и т. п. Граф вызовов LSP таких мест не видит (ЗИ-34).

Признак вызова — строковый литерал, целиком равный имени метода в нотации платформы
(без учёта регистра): «Модуль.Метод» для общего модуля, «Документы.Имя.Метод» для модуля
менеджера. Комментарии и строки многострочного литерала (текст запроса) не учитываются.
Pure: на входе строки модуля, на выходе номера строк и объемлющие методы."""
from __future__ import annotations

from dataclasses import dataclass

from .checks.bsl_module import ModuleModel

# Вид объекта (рус., как в полном имени) → коллекция менеджеров платформы.
_MANAGERS = {
    "Справочник": "Справочники",
    "Документ": "Документы",
    "Обработка": "Обработки",
    "Отчет": "Отчеты",
    "РегистрСведений": "РегистрыСведений",
    "РегистрНакопления": "РегистрыНакопления",
    "РегистрБухгалтерии": "РегистрыБухгалтерии",
    "РегистрРасчета": "РегистрыРасчета",
    "ПланВидовХарактеристик": "ПланыВидовХарактеристик",
    "ПланСчетов": "ПланыСчетов",
    "ПланВидовРасчета": "ПланыВидовРасчета",
    "ПланОбмена": "ПланыОбмена",
    "БизнесПроцесс": "БизнесПроцессы",
    "Задача": "Задачи",
    "Перечисление": "Перечисления",
    "ЖурналДокументов": "ЖурналыДокументов",
}


def literal_names(full_name: str) -> list[str]:
    """Строковые имена, которыми метод вызывают по имени: 'ОбщийМодуль.М.Метод' → ['М.Метод'],
    'Документ.Х.Метод' → ['Документы.Х.Метод']. Формы, команды, прочие виды — пусто."""
    parts = full_name.split(".")
    if len(parts) != 3:
        return []
    kind, obj, method = parts
    if kind in ("ОбщийМодуль", "CommonModule"):
        return [f"{obj}.{method}"]
    manager = _MANAGERS.get(kind)
    return [f"{manager}.{obj}.{method}"] if manager else []


def string_literals(line: str) -> list[str]:
    """Строковые литералы строки кода — каждый отдельно (удвоенная кавычка — символ).
    Комментарий и продолжение многострочного литерала (`|`) — пусто."""
    trimmed = line.lstrip()
    if trimmed.startswith("//") or trimmed.startswith("|"):
        return []
    out: list[str] = []
    buf: list[str] | None = None
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if buf is not None:
            if ch == '"':
                if i + 1 < n and line[i + 1] == '"':
                    buf.append('"')
                    i += 2
                    continue
                out.append("".join(buf))
                buf = None
            else:
                buf.append(ch)
        elif ch == '"':
            buf = []
        elif ch == "/" and i + 1 < n and line[i + 1] == "/":
            break
        i += 1
    return out          # незакрытый литерал (начало многострочного) — не имя метода


@dataclass(frozen=True)
class StringCall:
    line: int               # 1-based
    method: str | None      # объемлющий метод; None — код вне методов (тело модуля)
    kind: str | None        # "Процедура" | "Функция"


def find_string_calls(lines: list[str], names: list[str]) -> list[StringCall]:
    """Строки модуля, где литерал целиком равен одному из имён (без учёта регистра)."""
    wanted = {n.casefold() for n in names}
    if not wanted:
        return []
    hits = [n for n, text in enumerate(lines, start=1)
            if any(lit.strip().casefold() in wanted for lit in string_literals(text))]
    if not hits:
        return []
    methods = ModuleModel(lines).methods
    out = []
    for n in hits:
        m = next((m for m in methods if m.start <= n <= m.end), None)
        out.append(StringCall(n, m.name if m else None, m.kind if m else None))
    return out
