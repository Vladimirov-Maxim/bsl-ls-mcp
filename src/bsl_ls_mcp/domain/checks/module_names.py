"""Путь модуля в исходниках ↔ имя 1С. Оба формата: выгрузка конфигуратора
(`<Вид>/<Объект>/Ext/<Модуль>.bsl`, формы — `…/Forms/<Форма>/Ext/Form/Module.bsl`) и
проект EDT (`<Вид>/<Объект>/<Модуль>.bsl`, формы — `…/Forms/<Форма>/Module.bsl`).
Путь может начинаться с префикса до корня конфигурации (`BF/src/…` в репозитории EDT)."""
from __future__ import annotations

from ..one_c_config_path import _OBJECT_TYPE_MAP
from ..one_c_naming import type_en_to_ru

_MODULE_KIND_RU = {
    "ManagerModule.bsl": "МодульМенеджера",
    "ObjectModule.bsl": "МодульОбъекта",
    "RecordSetModule.bsl": "МодульНабораЗаписей",
    "ValueManagerModule.bsl": "МодульМенеджераЗначения",
    "CommandModule.bsl": "МодульКоманды",
}


def common_module_paths(name: str, prefixes: tuple[str, ...] = ("",)) -> list[str]:
    """Возможные пути модуля общего модуля: под каждым корнем конфигурации, в обоих
    форматах (сначала конфигуратор, затем EDT). Префикс — '' или 'BF/src/'."""
    return [f"{p}CommonModules/{name}/{tail}" for p in prefixes for tail in ("Ext/Module.bsl", "Module.bsl")]


def common_module_path(name: str) -> str:
    """Модуль общего модуля по имени в выгрузке конфигуратора (совместимость)."""
    return common_module_paths(name)[0]


def module_label(path: str) -> str:
    """'Documents/Заказ/Ext/ObjectModule.bsl' → 'Документ.Заказ.МодульОбъекта'
    (так же 'BF/src/Documents/Заказ/ObjectModule.bsl'). Нераспознанный путь
    (внешняя обработка, глобальный модуль) — сам путь."""
    all_parts = path.split("/")
    start = next((i for i, part in enumerate(all_parts) if part in _OBJECT_TYPE_MAP), None)
    if start is None:
        return path
    parts = all_parts[start:]
    type_en = _OBJECT_TYPE_MAP[parts[0]]
    type_ru = type_en_to_ru(type_en)
    if type_ru is None or len(parts) < 3:
        return path
    name = parts[1]
    if type_en in ("CommonModule", "CommonForm"):
        return f"{type_ru}.{name}"
    if len(parts) >= 4 and parts[2] == "Forms":
        return f"{type_ru}.{name}.Форма.{parts[3]}"
    if len(parts) >= 4 and parts[2] == "Commands":
        return f"{type_ru}.{name}.Команда.{parts[3]}"
    kind = _MODULE_KIND_RU.get(parts[-1])
    return f"{type_ru}.{name}.{kind}" if kind else f"{type_ru}.{name}"
