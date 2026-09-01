"""Разбор пути файла внутри CR-выгрузки 1С-конфигурации.

Разбор путей 1С — pure, без IO. Даёт path→(object_type EN,
object_name, module_kind) и карту каталог↔тип — используется resolver/mapper.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePath


@dataclass(frozen=True)
class OneCConfigContext:
    source_relative_path: str
    object_type: str | None = None      # английский канон (CommonModule/Document/...)
    object_name: str | None = None
    module_kind: str | None = None      # Module/ManagerModule/ObjectModule/FormModule/...
    form_name: str | None = None
    language: str | None = None


# Имя директории первого уровня → семантический object_type (EN). Стандарт CR-выгрузки.
_OBJECT_TYPE_MAP: dict[str, str] = {
    "CommonModules": "CommonModule",
    "Catalogs": "Catalog",
    "Constants": "Constant",
    "Documents": "Document",
    "InformationRegisters": "InformationRegister",
    "AccumulationRegisters": "AccumulationRegister",
    "CalculationRegisters": "CalculationRegister",
    "AccountingRegisters": "AccountingRegister",
    "Reports": "Report",
    "DataProcessors": "DataProcessor",
    "Enums": "Enum",
    "BusinessProcesses": "BusinessProcess",
    "Tasks": "Task",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "ChartsOfAccounts": "ChartOfAccounts",
    "ChartsOfCalculationTypes": "ChartOfCalculationTypes",
    "DocumentJournals": "DocumentJournal",
    "ExchangePlans": "ExchangePlan",
    "FilterCriteria": "FilterCriterion",
    "HTTPServices": "HTTPService",
    "WebServices": "WebService",
    "SettingsStorages": "SettingsStorage",
    "Subsystems": "Subsystem",
    "CommonForms": "CommonForm",
    "CommonCommands": "CommonCommand",
    "CommonTemplates": "CommonTemplate",
    "CommonPictures": "CommonPicture",
    "CommonAttributes": "CommonAttribute",
    "CommandGroups": "CommandGroup",
    "DefinedTypes": "DefinedType",
    "EventSubscriptions": "EventSubscription",
    "FunctionalOptions": "FunctionalOption",
    "FunctionalOptionsParameters": "FunctionalOptionsParameter",
    "Languages": "Language",
    "Roles": "Role",
    "ScheduledJobs": "ScheduledJob",
    "SessionParameters": "SessionParameter",
    "StyleItems": "StyleItem",
    "XDTOPackages": "XDTOPackage",
}

# Имя bsl-файла → module_kind. (Forms/<form>/ обрабатывается отдельно как FormModule.)
_MODULE_KIND_BY_FILENAME: dict[str, str] = {
    "Module.bsl": "Module",
    "ManagerModule.bsl": "ManagerModule",
    "ObjectModule.bsl": "ObjectModule",
    "RecordSetModule.bsl": "RecordSetModule",
    "ValueManagerModule.bsl": "ValueManagerModule",
    "CommandModule.bsl": "CommandModule",
    "Template.bsl": "Template",
}

# Глобальные модули приложения лежат прямо в ``Ext/`` (без родительского объекта).
_GLOBAL_APPLICATION_MODULE_KIND: dict[str, str] = {
    "ManagedApplicationModule.bsl": "ManagedApplicationModule",
    "OrdinaryApplicationModule.bsl": "OrdinaryApplicationModule",
    "SessionModule.bsl": "SessionModule",
    "ExternalConnectionModule.bsl": "ExternalConnectionModule",
}

# Обратная карта: object_type EN → имя type-директории (для resolver: имя → путь).
_TYPE_EN_TO_DIR: dict[str, str] = {en: d for d, en in _OBJECT_TYPE_MAP.items()}


class OneCConfigPathParser:
    """Stateless: разбирает путь файла относительно корня CR-выгрузки."""

    def parse(self, *, root: Path, file_path: Path) -> OneCConfigContext:
        rel = PurePath(file_path).relative_to(PurePath(root))
        rel_posix = rel.as_posix()
        parts = rel.parts

        if not parts:
            return OneCConfigContext(source_relative_path=rel_posix)

        if (
            parts[0] == "Ext"
            and len(parts) == 2
            and rel.name in _GLOBAL_APPLICATION_MODULE_KIND
        ):
            return OneCConfigContext(
                source_relative_path=rel_posix,
                object_type="GlobalModule",
                object_name=rel.stem,
                module_kind=_GLOBAL_APPLICATION_MODULE_KIND[rel.name],
            )

        object_type = _OBJECT_TYPE_MAP.get(parts[0])
        object_name = PurePath(parts[1]).stem if len(parts) >= 2 else None

        form_name: str | None = None
        if len(parts) >= 4 and parts[2] == "Forms":
            form_name = parts[3]

        suffix = rel.suffix.lower()
        module_kind: str | None = None
        language: str | None = None

        if suffix == ".bsl":
            if form_name is not None:
                module_kind = "FormModule"
            elif object_type == "CommonForm" and "Form" in parts:
                module_kind = "FormModule"
            else:
                module_kind = _MODULE_KIND_BY_FILENAME.get(rel.name)
        elif suffix == ".html":
            language = rel.stem.lower()

        return OneCConfigContext(
            source_relative_path=rel_posix,
            object_type=object_type,
            object_name=object_name,
            module_kind=module_kind,
            form_name=form_name,
            language=language,
        )
