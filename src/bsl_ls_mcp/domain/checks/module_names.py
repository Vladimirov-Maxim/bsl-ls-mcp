"""Путь модуля в выгрузке ↔ имя 1С. Формат выгрузки конфигуратора (Designer):
`<Вид>/<Объект>/Ext/<Модуль>.bsl`, формы — `…/Forms/<Форма>/Ext/Form/Module.bsl`."""
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


def common_module_path(name: str) -> str:
    """Модуль общего модуля по имени."""
    return f"CommonModules/{name}/Ext/Module.bsl"


def module_label(path: str) -> str:
    """'Documents/Заказ/Ext/ObjectModule.bsl' → 'Документ.Заказ.МодульОбъекта'.
    Нераспознанный путь (внешняя обработка, глобальный модуль) — сам путь."""
    parts = path.split("/")
    type_en = _OBJECT_TYPE_MAP.get(parts[0]) if parts else None
    type_ru = type_en_to_ru(type_en) if type_en else None
    if type_ru is None or len(parts) < 3:
        return path
    name = parts[1]
    if type_en == "CommonModule":
        return f"{type_ru}.{name}"
    if type_en == "CommonForm":
        return f"{type_ru}.{name}"
    if len(parts) >= 4 and parts[2] == "Forms":
        return f"{type_ru}.{name}.Форма.{parts[3]}"
    if len(parts) >= 4 and parts[2] == "Commands":
        return f"{type_ru}.{name}.Команда.{parts[3]}"
    kind = _MODULE_KIND_RU.get(parts[-1])
    return f"{type_ru}.{name}.{kind}" if kind else f"{type_ru}.{name}"
