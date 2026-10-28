"""Мини-проект для диагностик кода задачи: модули задачи + описания их объектов и форм.

analyze BSL LS видит тип модуля и реквизиты формы, только если каталог — проект: выгрузка
конфигуратора (`Configuration.xml`) или проект EDT (`DT-INF/PROJECT.PMF` + `src/Configuration/
Configuration.mdo`; без `DT-INF` EDT-раскладку analyze не распознаёт — проверено на 1.0.7). Без
описания формы реквизиты выглядят неиспользуемыми переменными, обработчики — неиспользуемыми
методами. Поэтому в проект кладутся не только модули задачи, но и описания их объектов и форм.

Здесь — только пути и тексты: какие описания нужны модулю и где они лежат в проекте. Чтение
источника правок и диск — у вызывающего."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import external_objects as eo

EDT = "edt"
DESIGNER = "designer"
DEFAULT_PMF = "Manifest-Version: 1.0\nRuntime-Version: 8.3.24\n"

# Диагностики метаданных BSL LS (1.0.7: наследники AbstractMetadataDiagnostic и проверки имён общих
# модулей). Предмет — свойство в описании объекта, а BSL LS ставит их на начало модуля объекта.
# ScheduledJobHandler не входит: его предмет — регламентное задание, у которого модуля нет.
METADATA_DIAGNOSTICS: frozenset[str] = frozenset({
    "DenyIncompleteValues", "ForbiddenMetadataName", "MetadataObjectNameLength",
    "SameMetadataObjectAndChildNames", "CommonModuleInvalidType", "CommonModuleNameCached",
    "CommonModuleNameClient", "CommonModuleNameClientServer", "CommonModuleNameFullAccess",
    "CommonModuleNameGlobal", "CommonModuleNameGlobalClient", "CommonModuleNameServerCall",
    "CommonModuleNameWords",
})

_QUOTED = re.compile(r'"([^"]+)"|`([^`]+)`')

# Виды объектов — таблиц запроса (рус. и англ. имя → каталог в исходниках). Объект, на который
# ссылается запрос модуля, а описания его в проекте нет, BSL LS считает несуществующим
# (`QueryToMissingMetadata`): проверка ищет объект в конфигурации, полей не касается.
_QUERY_TABLE_DIRS: dict[str, str] = {
    "Справочник": "Catalogs", "Catalog": "Catalogs",
    "Документ": "Documents", "Document": "Documents",
    "ЖурналДокументов": "DocumentJournals", "DocumentJournal": "DocumentJournals",
    "Перечисление": "Enums", "Enum": "Enums",
    "Константа": "Constants", "Constant": "Constants",
    "РегистрСведений": "InformationRegisters", "InformationRegister": "InformationRegisters",
    "РегистрНакопления": "AccumulationRegisters", "AccumulationRegister": "AccumulationRegisters",
    "РегистрБухгалтерии": "AccountingRegisters", "AccountingRegister": "AccountingRegisters",
    "РегистрРасчета": "CalculationRegisters", "CalculationRegister": "CalculationRegisters",
    "ПланСчетов": "ChartsOfAccounts", "ChartOfAccounts": "ChartsOfAccounts",
    "ПланВидовХарактеристик": "ChartsOfCharacteristicTypes",
    "ChartOfCharacteristicTypes": "ChartsOfCharacteristicTypes",
    "ПланВидовРасчета": "ChartsOfCalculationTypes", "ChartOfCalculationTypes": "ChartsOfCalculationTypes",
    "ПланОбмена": "ExchangePlans", "ExchangePlan": "ExchangePlans",
    "БизнесПроцесс": "BusinessProcesses", "BusinessProcess": "BusinessProcesses",
    "Задача": "Tasks", "Task": "Tasks",
    "КритерийОтбора": "FilterCriteria", "FilterCriterion": "FilterCriteria",
    "Последовательность": "Sequences", "Sequence": "Sequences",
}
_QUERY_TABLE = re.compile(
    r"(?<![\w.])(" + "|".join(sorted(_QUERY_TABLE_DIRS, key=len, reverse=True)) + r")\.(\w+)", re.IGNORECASE)
_QUERY_TABLE_KEYS = {k.casefold(): v for k, v in _QUERY_TABLE_DIRS.items()}

# Каталоги внешних объектов EDT (проект внешней обработки/отчёта).
_EDT_EXTERNAL_DIRS = {"externaldataprocessors": "ExternalDataProcessor", "externalreports": "ExternalReport"}


@dataclass(frozen=True)
class Project:
    """Один мини-проект: корень конфигурации в репозитории (`prefix`, напр. 'App/src/'), формат и —
    для внешнего объекта без конфигурации — его вид и имя."""
    prefix: str
    fmt: str                                   # EDT | DESIGNER
    external: eo.ExternalKind | None = None
    name: str = ""                             # имя внешнего объекта

    @property
    def src(self) -> str:
        """Каталог исходников внутри мини-проекта."""
        return "src/" if self.fmt == EDT else ""


def split_prefix(path: str, prefixes: tuple[str, ...]) -> tuple[str, str]:
    """(prefix, путь от корня конфигурации) — по самому длинному подходящему префиксу."""
    best = ""
    for p in prefixes:
        if p and path.casefold().startswith(p.casefold()) and len(p) > len(best):
            best = p
    return best, path[len(best):]


def configuration_paths(prefix: str) -> dict[str, str]:
    """Описание конфигурации по формату: EDT → (Configuration.mdo, PROJECT.PMF рядом с src)."""
    edt_root = prefix[: -len("src/")] if prefix.casefold().endswith("src/") else prefix
    return {EDT: f"{prefix}Configuration/Configuration.mdo", DESIGNER: f"{prefix}Configuration.xml",
            "pmf": f"{edt_root}DT-INF/PROJECT.PMF"}


def external_candidates(rel: str) -> list[tuple[str, str, bool]]:
    """Где может лежать описание внешнего объекта модуля `rel` (путь от корня каталога):
    [(путь описания, имя объекта, EDT?)]. Конфигуратор: `<Имя>/Ext/ObjectModule.bsl` → `<Имя>.xml`;
    EDT: `ExternalDataProcessors/<Имя>/…` → `ExternalDataProcessors/<Имя>/<Имя>.mdo`."""
    parts = rel.split("/")
    out: list[tuple[str, str, bool]] = []
    if len(parts) >= 3 and parts[0].casefold() in _EDT_EXTERNAL_DIRS:
        out.append((f"{parts[0]}/{parts[1]}/{parts[1]}.mdo", parts[1], True))
    if len(parts) >= 2:
        out.append((f"{parts[0]}.xml", parts[0], False))
    return out


def descriptions(rel: str, project: Project) -> list[str]:
    """Описания (пути от корня конфигурации), которые нужны модулю `rel` того же корня: объект и,
    для модуля формы, описание формы. Порядок — от объекта к форме; лишние пути вызывающий отбросит,
    если их нет в источнике."""
    parts = rel.split("/")
    if project.external is not None:
        if project.fmt == EDT:                 # ExternalDataProcessors/<Имя>/[Forms/<Ф>/]Module.bsl
            obj, inner = "/".join(parts[:2]), parts[2:]
            out = [f"{obj}/{parts[1]}.mdo"]
            if len(inner) >= 3 and inner[0].casefold() == "forms":
                out.append(f"{obj}/Forms/{inner[1]}/Form.form")
            return out
        name, inner = parts[0], parts[1:]      # <Имя>/[Ext|Forms/<Ф>/Ext/Form]/…
        out = [f"{name}.xml"]
        if len(inner) >= 2 and inner[0].casefold() == "forms":
            out += [f"{name}/Forms/{inner[1]}.xml", f"{name}/Forms/{inner[1]}/Ext/Form.xml"]
        return out
    if len(parts) < 3:
        return []
    kind, name = parts[0], parts[1]
    if project.fmt == EDT:
        out = [f"{kind}/{name}/{name}.mdo"]
        if kind.casefold() == "commonforms":
            out.append(f"{kind}/{name}/Form.form")
        elif len(parts) >= 5 and parts[2].casefold() == "forms":
            out.append(f"{kind}/{name}/Forms/{parts[3]}/Form.form")
        return out
    out = [f"{kind}/{name}.xml"]
    if kind.casefold() == "commonforms":
        out.append(f"{kind}/{name}/Ext/Form.xml")
    elif len(parts) >= 5 and parts[2].casefold() == "forms":
        out += [f"{kind}/{name}/Forms/{parts[3]}.xml", f"{kind}/{name}/Forms/{parts[3]}/Ext/Form.xml"]
    return out


def project_path(rel: str, project: Project) -> str:
    """Где файл `rel` (путь от корня конфигурации) лежит в мини-проекте. Внешний объект
    переезжает в каталог объекта конфигурации (`DataProcessors/<Имя>/…`)."""
    if project.external is None:
        return project.src + rel
    target = project.external.config_dir
    if project.fmt == EDT:                     # ExternalDataProcessors/<Имя>/… → DataProcessors/<Имя>/…
        return project.src + target + "/" + rel.split("/", 1)[1]
    return target + "/" + rel                  # <Имя>.xml, <Имя>/… → DataProcessors/<Имя>.xml, …/<Имя>/…


def transform(text: str, project: Project) -> str:
    """Текст описания для мини-проекта: у внешнего объекта — как у объекта конфигурации."""
    return eo.as_config_object(text) if project.external is not None else text


def external_configuration(project: Project, root_text: str) -> tuple[str, str]:
    """(путь, текст) описания конфигурации с одним внешним объектом."""
    if project.fmt == EDT:
        return "src/Configuration/Configuration.mdo", eo.edt_configuration(project.name, project.external)
    return "Configuration.xml", eo.designer_configuration(project.name, project.external, root_text)


def metadata_line(description: str, message: str) -> int | None:
    """Строка описания объекта, к которой относится диагностика метаданных: имя из кавычек сообщения
    (измерение, реквизит, сам объект) — строка `<name>Имя</name>` (EDT) или `<Name>Имя</Name>`
    (конфигуратор). Берётся первое имя сообщения, которое есть в описании, и последнее его вхождение:
    у реквизита, одноимённого с объектом, это строка реквизита. None — имени в описании нет."""
    lines = description.splitlines()
    for m in _QUOTED.finditer(message):
        name = (m.group(1) or m.group(2)).rsplit(".", 1)[-1]
        tag = re.compile(rf"<(?:name|Name)>\s*{re.escape(name)}\s*</(?:name|Name)>")
        hits = [n for n, line in enumerate(lines, 1) if tag.search(line)]
        if hits:
            return hits[-1]
    return None


def query_objects(text: str) -> list[tuple[str, str]]:
    """Объекты, на которые ссылается текст модуля как на таблицы запроса (`Документ.Заказ`,
    `РегистрСведений.Курсы.СрезПоследних`, `ЗНАЧЕНИЕ(Перечисление.Х.У)`): [(каталог, имя)] без
    повторов. Ищется по всему тексту, не только в запросах: лишний объект проекту не вредит."""
    seen: dict[tuple[str, str], None] = {}
    for m in _QUERY_TABLE.finditer(text):
        seen[(_QUERY_TABLE_KEYS[m.group(1).casefold()], m.group(2))] = None
    return list(seen)


def object_description(kind_dir: str, name: str, project: Project) -> str:
    """Путь описания объекта конфигурации от её корня."""
    return f"{kind_dir}/{name}/{name}.mdo" if project.fmt == EDT else f"{kind_dir}/{name}.xml"
