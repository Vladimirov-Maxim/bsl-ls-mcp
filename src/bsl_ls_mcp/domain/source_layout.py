"""Раскладка исходников конфигурации: выгрузка конфигуратора (Designer) или проект EDT.

Разница — только в том, где внутри каталога объекта лежат модули:

    Designer                                    EDT
    <Вид>/<Объект>/Ext/<Модуль>.bsl             <Вид>/<Объект>/<Модуль>.bsl
    …/Forms/<Форма>/Ext/Form/Module.bsl         …/Forms/<Форма>/Module.bsl
    …/Commands/<Команда>/Ext/CommandModule.bsl  …/Commands/<Команда>/CommandModule.bsl
    CommonForms/<Форма>/Ext/Form/Module.bsl     CommonForms/<Форма>/Module.bsl
    Ext/SessionModule.bsl (модули приложения)   Configuration/SessionModule.bsl
    Configuration.xml в корне                   Configuration/Configuration.mdo

Каталоги видов (`CommonModules`, `Catalogs`…) и имена файлов модулей совпадают.
Pure: только сегменты путей; формат по диску определяет resolver.layout_of."""
from __future__ import annotations

DESIGNER = "designer"
EDT = "edt"

# Признак формата в корне исходников (сегменты от корня).
ROOT_MARKERS: dict[str, tuple[str, ...]] = {
    DESIGNER: ("Configuration.xml",),
    EDT: ("Configuration", "Configuration.mdo"),
}


def own_parts(layout: str) -> tuple[str, ...]:
    """Сегменты от каталога объекта до его собственных модулей (без форм и команд)."""
    return ("Ext",) if layout == DESIGNER else ()


def form_parts(layout: str, form: str) -> tuple[str, ...]:
    """Сегменты от каталога объекта до каталога модуля формы."""
    return ("Forms", form, "Ext", "Form") if layout == DESIGNER else ("Forms", form)


def command_parts(layout: str, command: str) -> tuple[str, ...]:
    """Сегменты от каталога объекта до каталога модуля команды."""
    return ("Commands", command, "Ext") if layout == DESIGNER else ("Commands", command)


def module_names(layout: str, type_en: str) -> tuple[str, ...]:
    """Файлы собственных модулей объекта вида `type_en` относительно own_parts —
    в порядке перебора при поиске символа."""
    if type_en == "CommonForm":
        return ("Form/Module.bsl",) if layout == DESIGNER else ("Module.bsl",)
    return _MODULES.get(type_en, _MODULES["_default"])


_MODULES: dict[str, tuple[str, ...]] = {
    "CommonModule": ("Module.bsl",),
    "CommonCommand": ("CommandModule.bsl",),
    "HTTPService": ("Module.bsl",),
    "WebService": ("Module.bsl",),
    "IntegrationService": ("Module.bsl",),
    "_default": (
        "ManagerModule.bsl",
        "ObjectModule.bsl",
        "RecordSetModule.bsl",
        "ValueManagerModule.bsl",
    ),
}
