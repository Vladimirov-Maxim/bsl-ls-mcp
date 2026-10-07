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
Формат определяется по корню; всё остальное спрашивает отсюда, а не собирает пути само."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

DESIGNER = "designer"
EDT = "edt"


def detect(root: Path) -> str:
    """Формат исходников по корню. Без корневого файла (часть выгрузки, фикстуры) —
    по первому общему модулю; совсем без признаков — Designer (прежнее поведение)."""
    return _detect_cached(str(Path(root)))


@lru_cache(maxsize=32)
def _detect_cached(root_str: str) -> str:
    root = Path(root_str)
    if (root / "Configuration.xml").is_file():
        return DESIGNER
    if (root / "Configuration" / "Configuration.mdo").is_file():
        return EDT
    common = root / "CommonModules"
    if common.is_dir():
        for d in common.iterdir():
            if (d / "Ext").is_dir():
                return DESIGNER
            if (d / "Module.bsl").is_file():
                return EDT
    return DESIGNER


def own_dir(layout: str, base: Path) -> Path:
    """Каталог собственных модулей объекта (без форм и команд)."""
    return base / "Ext" if layout == DESIGNER else base


def form_dir(layout: str, base: Path, form: str) -> Path:
    """Каталог модуля формы объекта."""
    d = base / "Forms" / form
    return d / "Ext" / "Form" if layout == DESIGNER else d


def command_dir(layout: str, base: Path, command: str) -> Path:
    """Каталог модуля команды объекта."""
    d = base / "Commands" / command
    return d / "Ext" if layout == DESIGNER else d


def module_names(layout: str, type_en: str) -> tuple[str, ...]:
    """Файлы собственных модулей объекта вида `type_en` относительно own_dir —
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

# Каталог модулей приложения (сеанса, внешнего соединения…) в корне исходников.
GLOBAL_MODULE_DIRS = ("Ext", "Configuration")
