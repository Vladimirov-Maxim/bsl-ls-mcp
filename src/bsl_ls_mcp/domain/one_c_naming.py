"""Таксономия видов метаданных 1С: английский канон ↔ русский доменный язык.

Pure-знание без IO. Английский канон = имена type-директорий CR-выгрузки.
Русский — то, что возвращает `ПолноеИмя()` и использует БСП.
"""
from __future__ import annotations

import re

# Английский канон → русский вид метаданных (полный охват значений _OBJECT_TYPE_MAP).
_EN_TO_RU: dict[str, str] = {
    "CommonModule": "ОбщийМодуль",
    "Catalog": "Справочник",
    "Constant": "Константа",
    "Document": "Документ",
    "InformationRegister": "РегистрСведений",
    "AccumulationRegister": "РегистрНакопления",
    "CalculationRegister": "РегистрРасчета",
    "AccountingRegister": "РегистрБухгалтерии",
    "Report": "Отчет",
    "DataProcessor": "Обработка",
    "Enum": "Перечисление",
    "BusinessProcess": "БизнесПроцесс",
    "Task": "Задача",
    "ChartOfCharacteristicTypes": "ПланВидовХарактеристик",
    "ChartOfAccounts": "ПланСчетов",
    "ChartOfCalculationTypes": "ПланВидовРасчета",
    "DocumentJournal": "ЖурналДокументов",
    "ExchangePlan": "ПланОбмена",
    "FilterCriterion": "КритерийОтбора",
    "HTTPService": "HTTPСервис",
    "WebService": "WebСервис",
    "SettingsStorage": "ХранилищеНастроек",
    "Subsystem": "Подсистема",
    "CommonForm": "ОбщаяФорма",
    "CommonCommand": "ОбщаяКоманда",
    "CommonTemplate": "ОбщийМакет",
    "CommonPicture": "ОбщаяКартинка",
    "CommonAttribute": "ОбщийРеквизит",
    "CommandGroup": "ГруппаКоманд",
    "DefinedType": "ОпределяемыйТип",
    "EventSubscription": "ПодпискаНаСобытие",
    "FunctionalOption": "ФункциональнаяОпция",
    "FunctionalOptionsParameter": "ПараметрФункциональныхОпций",
    "Language": "Язык",
    "Role": "Роль",
    "ScheduledJob": "РегламентноеЗадание",
    "SessionParameter": "ПараметрСеанса",
    "StyleItem": "ЭлементСтиля",
    "XDTOPackage": "ПакетXDTO",
}

# EN→RU обратный для перевода русского имени типа обратно в канон.
_RU_TO_EN: dict[str, str] = {ru: en for en, ru in _EN_TO_RU.items()}

# Каноничные EN-типы, отсортированы по убыванию длины — для longest-prefix-match
# (``DocumentJournalManager`` должен дать ``DocumentJournal``, не ``Document``).
_EN_TYPES_BY_LEN: tuple[str, ...] = tuple(sorted(_EN_TO_RU, key=len, reverse=True))

_NS_PREFIX_RE = re.compile(r"^\w+:")


def ref_prefix_to_type_en(prefix: str | None) -> str | None:
    """Префикс ссылки (`v8:Type`/MDObjectRef) → каноничный английский тип по
    самому длинному совпадению начала. Неизвестное → None."""
    if not prefix:
        return None
    for type_en in _EN_TYPES_BY_LEN:
        if prefix.startswith(type_en):
            return type_en
    return None


def type_en_to_ru(type_en: str | None) -> str | None:
    """Английский канон → русский вид. Неизвестное/None → None."""
    return _EN_TO_RU.get(type_en) if type_en else None


def type_ru_to_en(type_ru: str | None) -> str | None:
    """Русский вид → английский канон. Неизвестное/None → None."""
    return _RU_TO_EN.get(type_ru) if type_ru else None


def full_name(type_ru: str | None, name: str) -> str:
    """Полное имя объекта: ``Справочник.Валюта``. Неизвестный тип → имя как есть."""
    return f"{type_ru}.{name}" if type_ru else name


def resolve_object_name(raw: str | None) -> tuple[str, str | None] | None:
    """Имя + EN-тип цели из ссылки 1С (3 формата): ``cfg:CatalogRef.X`` /
    ``Catalog.X`` (MDObjectRef) / ``InformationRegister.X.Resource.Y`` (FQN).
    Возвращает ``(name, type_en)``; примитив (``xs:string`` — нет точки) → None."""
    if not raw:
        return None
    text = _NS_PREFIX_RE.sub("", raw.strip())
    parts = text.split(".")
    if len(parts) >= 2 and parts[1]:
        return parts[1], ref_prefix_to_type_en(parts[0])
    return None
