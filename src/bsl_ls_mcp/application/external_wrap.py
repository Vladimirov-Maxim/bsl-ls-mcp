"""Внешняя обработка/отчёт вне workspace → временная конфигурация для analyze (ЗИ-70).

Тексты преобразует `domain.external_objects`; здесь — поиск объекта по пути и копирование.
Модули `.bsl` копируются байт в байт; ключ отображения — раскрытый путь копии (casefold),
значение — URI исходного файла, как у `_mirror` в tools."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ..domain import external_objects as eo
from ..domain.task_project import DEFAULT_PMF

_TEXT_SUFFIXES = {".xml", ".mdo", ".form"}
_MAX_UP = 8          # на сколько уровней вверх от пути искать корень объекта


@dataclass(frozen=True)
class ExternalObject:
    kind: eo.ExternalKind
    name: str
    object_dir: Path        # каталог объекта: <корень>/<Имя> (конфигуратор) или <…>/<Имя> (EDT)
    description: Path       # <корень>/<Имя>.xml (конфигуратор) или <Имя>/<Имя>.mdo (EDT)

    def label(self, original: Path) -> str | None:
        """'ВнешняяОбработка.Имя.Форма.Ф' / '….МодульОбъекта' для файла объекта."""
        try:
            rel = original.resolve().relative_to(self.object_dir.resolve())
        except ValueError:
            return None
        return eo.module_label(self.kind, self.name, rel.parts)


def _read(p: Path) -> str:
    try:
        with p.open(encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(4000)
    except OSError:
        return ""


def find_external(path: Path) -> ExternalObject | None:
    """Внешний объект, которому принадлежит путь (файл модуля, каталог формы, каталог объекта
    или корень выгрузки с `<Имя>.xml`). None — путь не внутри внешней обработки/отчёта."""
    p = path.resolve()
    start = p if p.is_dir() else p.parent
    for d in [start, *start.parents][:_MAX_UP]:
        xml = d.parent / f"{d.name}.xml"
        if xml.is_file() and (kind := eo.kind_of_designer(_read(xml))):
            return ExternalObject(kind, d.name, d, xml)
        mdo = d / f"{d.name}.mdo"
        if mdo.is_file() and (kind := eo.kind_of_edt(_read(mdo))):
            return ExternalObject(kind, d.name, d, mdo)
    if p.is_dir():                       # корень выгрузки конфигуратора: <Имя>.xml + <Имя>/
        for xml in sorted(p.glob("*.xml")):
            if (p / xml.stem).is_dir() and (kind := eo.kind_of_designer(_read(xml))):
                return ExternalObject(kind, xml.stem, p / xml.stem, xml)
    return None


def wrap(ext: ExternalObject, tmp_dir: Path) -> dict[str, str]:
    """Развернуть в tmp_dir конфигурацию с одним объектом — копией внешнего. Возвращает
    отображение копий модулей на URI исходных файлов."""
    kind = ext.kind
    # EDT: analyze распознаёт проект только с DT-INF/PROJECT.PMF и исходниками в src/
    src = tmp_dir / "src" if kind.edt else tmp_dir
    target = src / kind.config_dir / ext.name
    if kind.edt:
        (tmp_dir / "DT-INF").mkdir(parents=True)
        (tmp_dir / "DT-INF" / "PROJECT.PMF").write_text(DEFAULT_PMF, encoding="utf-8")
        (src / "Configuration").mkdir(parents=True)
        (src / "Configuration" / "Configuration.mdo").write_text(
            eo.edt_configuration(ext.name, kind), encoding="utf-8")
    else:
        root_text = ext.description.read_text(encoding="utf-8-sig", errors="replace")
        (tmp_dir / kind.config_dir).mkdir(parents=True)
        (tmp_dir / "Configuration.xml").write_text(
            eo.designer_configuration(ext.name, kind, root_text), encoding="utf-8")
        (tmp_dir / kind.config_dir / f"{ext.name}.xml").write_text(
            eo.as_config_object(root_text), encoding="utf-8")
    mirror: dict[str, str] = {}
    for src in ext.object_dir.rglob("*"):
        if not src.is_file():
            continue
        dst = target / src.relative_to(ext.object_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix.casefold() in _TEXT_SUFFIXES:
            dst.write_text(eo.as_config_object(src.read_text(encoding="utf-8-sig", errors="replace")),
                           encoding="utf-8")
        else:
            shutil.copyfile(src, dst)
            if src.suffix.casefold() == ".bsl":
                mirror[str(dst.resolve()).casefold()] = src.resolve().as_uri()
    return mirror
