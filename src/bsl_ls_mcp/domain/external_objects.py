"""Внешняя обработка и внешний отчёт как объект конфигурации — чистые преобразования текста.

BSL LS разбирает описание форм только у объектов конфигурации: у внешней обработки, которую
проверяют по `path`, он видит один `.bsl`, и реквизиты формы выглядят необъявленными
переменными, а обработчики из описания формы — неиспользуемыми методами. Поэтому
внешний объект оборачивается во временную конфигурацию: тот же каталог объекта, но
`ExternalDataProcessor` → `DataProcessor`, `ExternalReport` → `Report` во всех описаниях
(корень, ссылки на формы, типы `…Object.Имя` в реквизитах форм), плюс `Configuration`
с одним объектом. Диск — у вызывающего; здесь только тексты и имена."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Вид внешнего объекта → (вид объекта конфигурации; каталог в выгрузке; русское имя типа).
KINDS: dict[str, tuple[str, str, str]] = {
    "ExternalDataProcessor": ("DataProcessor", "DataProcessors", "ВнешняяОбработка"),
    "ExternalReport": ("Report", "Reports", "ВнешнийОтчет"),
}

_DESIGNER_ROOT = re.compile(r"<MetaDataObject\b[^>]*>\s*<(ExternalDataProcessor|ExternalReport)\b")
_EDT_ROOT = re.compile(r"<mdclass:(ExternalDataProcessor|ExternalReport)\b")
_EXTERNAL = re.compile(r"\bExternal(DataProcessor|Report)")
_HEADER = re.compile(r"<MetaDataObject\b[^>]*>")

# Модуль внешнего объекта по пути от каталога объекта (части пути, без регистра имени файла).
_MODULES = {"objectmodule.bsl": "МодульОбъекта", "managermodule.bsl": "МодульМенеджера"}


@dataclass(frozen=True)
class ExternalKind:
    kind: str          # ExternalDataProcessor | ExternalReport
    edt: bool          # описание .mdo (EDT) или .xml (конфигуратор)

    @property
    def config_kind(self) -> str:
        return KINDS[self.kind][0]

    @property
    def config_dir(self) -> str:
        return KINDS[self.kind][1]

    @property
    def type_ru(self) -> str:
        return KINDS[self.kind][2]


def kind_of_designer(xml: str) -> ExternalKind | None:
    """Корневое описание выгрузки конфигуратора (`<Имя>.xml`) — внешний объект или нет."""
    m = _DESIGNER_ROOT.search(xml[:4000])
    return ExternalKind(m.group(1), edt=False) if m else None


def kind_of_edt(mdo: str) -> ExternalKind | None:
    """Описание EDT (`<Имя>/<Имя>.mdo`) — внешний объект или нет."""
    m = _EDT_ROOT.search(mdo[:4000])
    return ExternalKind(m.group(1), edt=True) if m else None


def as_config_object(text: str) -> str:
    """Описание внешнего объекта (корень, форма) → описание объекта конфигурации."""
    return _EXTERNAL.sub(r"\1", text)


def designer_configuration(name: str, kind: ExternalKind, root_xml: str) -> str:
    """`Configuration.xml` с одним объектом; пространства имён — из корня внешнего объекта."""
    header = _HEADER.search(root_xml)
    ns = header.group(0) if header else ('<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
                                         'version="2.17">')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n{ns}\n'
            '<Configuration uuid="00000000-0000-0000-0000-000000000001">\n'
            "<Properties><Name>ОбёрткаВнешнегоОбъекта</Name><ScriptVariant>Russian</ScriptVariant>"
            "<CompatibilityMode>DontUse</CompatibilityMode></Properties>\n"
            f"<ChildObjects>\n<{kind.config_kind}>{name}</{kind.config_kind}>\n</ChildObjects>\n"
            "</Configuration>\n</MetaDataObject>\n")


def edt_configuration(name: str, kind: ExternalKind) -> str:
    """`Configuration/Configuration.mdo` с одним объектом."""
    field = "dataProcessors" if kind.config_kind == "DataProcessor" else "reports"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<mdclass:Configuration xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" '
            'uuid="00000000-0000-0000-0000-000000000001">\n'
            "  <name>ОбёрткаВнешнегоОбъекта</name>\n  <scriptVariant>Russian</scriptVariant>\n"
            f"  <{field}>{kind.config_kind}.{name}</{field}>\n</mdclass:Configuration>\n")


def module_label(kind: ExternalKind, name: str, rel_parts: tuple[str, ...]) -> str | None:
    """Подпись модуля внешнего объекта по пути от каталога объекта:
    `Ext/ObjectModule.bsl` | `ObjectModule.bsl` → 'ВнешняяОбработка.Имя.МодульОбъекта';
    `Forms/Ф/Ext/Form/Module.bsl` | `Forms/Ф/Module.bsl` → 'ВнешняяОбработка.Имя.Форма.Ф'."""
    if not rel_parts:
        return None
    base = f"{kind.type_ru}.{name}"
    if len(rel_parts) >= 3 and rel_parts[0].casefold() == "forms" and rel_parts[-1].casefold() == "module.bsl":
        return f"{base}.Форма.{rel_parts[1]}"
    module = _MODULES.get(rel_parts[-1].casefold())
    if module and (len(rel_parts) == 1 or (len(rel_parts) == 2 and rel_parts[0].casefold() == "ext")):
        return f"{base}.{module}"
    return None
