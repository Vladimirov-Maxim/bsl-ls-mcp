"""Мини-проект для диагностик кода задачи: модули задачи + описания их объектов и форм.

analyze BSL LS видит тип модуля и реквизиты формы, только если каталог — проект: выгрузка
конфигуратора (`Configuration.xml`) или проект EDT (`DT-INF/PROJECT.PMF` + `src/Configuration/
Configuration.mdo`; без `DT-INF` EDT-раскладку analyze не распознаёт — проверено на 1.0.7). Без
описания формы реквизиты выглядят неиспользуемыми переменными, обработчики — неиспользуемыми
методами. Поэтому в проект кладутся не только модули задачи, но и описания их объектов и форм.

Здесь — только пути и тексты: какие описания нужны модулю и где они лежат в проекте. Чтение
источника правок и диск — у вызывающего."""
from __future__ import annotations

from dataclasses import dataclass

from . import external_objects as eo

EDT = "edt"
DESIGNER = "designer"
DEFAULT_PMF = "Manifest-Version: 1.0\nRuntime-Version: 8.3.24\n"

# Каталоги внешних объектов EDT (проект внешней обработки/отчёта).
_EDT_EXTERNAL_DIRS = {"externaldataprocessors": "ExternalDataProcessor", "externalreports": "ExternalReport"}


@dataclass(frozen=True)
class Project:
    """Один мини-проект: корень конфигурации в репозитории (`prefix`, напр. 'BF/src/'), формат и —
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
