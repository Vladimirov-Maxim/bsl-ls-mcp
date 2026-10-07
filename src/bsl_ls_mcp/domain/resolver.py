"""Резолв доменного имени 1С -> файлы-кандидаты модуля (детерминированно, по карте
каталогов исходников — выгрузки конфигуратора или проекта EDT, см.
source_layout). Точную ПОЗИЦИЮ символа спрашиваем у LSP-сервера
(documentSymbol) в application — без текстового угадывания по .bsl."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import source_layout
from .one_c_config_path import _TYPE_EN_TO_DIR
from .one_c_naming import type_ru_to_en

# Имя объекта/модуля/формы 1С — идентификатор: буквы (лат./кир.), цифры, подчёркивание.
# НИКАКИХ / \ : . пробелов — иначе сегмент имени уводит путь наружу (обход каталога,
# абсолютная замена базы, UNC). Валидируем каждый сегмент перед подстановкой в путь.
_SAFE_SEGMENT = re.compile(r"[0-9A-Za-z_Ѐ-ӿ]+")


def valid_segment(name: str) -> bool:
    """Безопасный сегмент имени (без разделителей пути и точек). fullmatch, а НЕ match+$:
    в Python '$' совпадает и перед хвостовым '\\n', т.е. 'Модуль\\n' прошёл бы — fullmatch
    требует, чтобы регулярка покрыла ВСЮ строку целиком."""
    return bool(_SAFE_SEGMENT.fullmatch(name))


def within_roots(path: Path, roots: Iterable[Path]) -> bool:
    """path (после resolve) лежит под одним из roots? Блокирует '..', абсолютную замену
    базы и UNC (иначе `path=\\\\host\\share` = исходящий SMB и утечка NTLM-хэша)."""
    if str(path).startswith(("\\\\", "//")):        # UNC до resolve
        return False
    try:
        rp = path.resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    if str(rp).startswith(("\\\\", "//")):           # resolve развернул в UNC
        return False
    for root in roots:
        try:
            rp.relative_to(Path(root).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False

def is_local_path(path: Path) -> bool:
    """Путь локальный (не UNC — ни до, ни после resolve)? Произвольный каталог
    допустим, сетевая шара — нет: обращение к `\\\\host\\share` от службы уводит
    NTLM-хэш сервисной учётной записи."""
    if str(path).startswith(("\\\\", "//")):
        return False
    try:
        rp = path.resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    return not str(rp).startswith(("\\\\", "//"))


@dataclass(frozen=True)
class Position:
    uri: str
    line: int       # 0-based (LSP)
    character: int


def _object_base(workspace: Path, type_ru: str, name: str) -> Path | None:
    """Каталог объекта по русскому виду и имени; None — неизвестный вид или небезопасное имя."""
    type_en = type_ru_to_en(type_ru)
    directory = _TYPE_EN_TO_DIR.get(type_en) if type_en else None
    if directory is None or not valid_segment(name):
        return None
    return workspace / directory / name


def _candidate_files(workspace: Path, type_en: str, module: str) -> list[Path]:
    directory = _TYPE_EN_TO_DIR.get(type_en)
    if directory is None or not valid_segment(module):
        return []
    layout = source_layout.detect(workspace)
    own = source_layout.own_dir(layout, workspace / directory / module)
    return [own / rel for rel in source_layout.module_names(layout, type_en)]


def candidate_uris(workspace: Path, type_ru: str, module: str) -> list[str]:
    """URI существующих файлов-кандидатов модуля (для запроса documentSymbol).
    Пусто — неизвестный тип или нет файлов."""
    type_en = type_ru_to_en(type_ru)
    if type_en is None:
        return []
    return [p.resolve().as_uri() for p in _candidate_files(workspace, type_en, module) if p.exists()]


def module_uri(workspace: Path, type_ru: str, module: str) -> str | None:
    """URI файла модуля (первый существующий кандидат) — для диагностик/сложности.
    Содержимое читает клиент при синхронизации, здесь только адрес."""
    type_en = type_ru_to_en(type_ru)
    if type_en is None:
        return None
    for fpath in _candidate_files(workspace, type_en, module):
        if fpath.exists():
            return fpath.resolve().as_uri()
    return None


# Маркеры подмодулей на 3-й позиции имени (одинаковы для всех функций ниже).
_FORM_MARK = {"Форма", "Form"}
_CMD_MARK = {"Команда", "Command"}
# Конкретный модуль прикладного объекта по русскому имени -> файл.
_KIND_FILE = {
    "МодульМенеджера": "ManagerModule.bsl", "ManagerModule": "ManagerModule.bsl",
    "МодульОбъекта": "ObjectModule.bsl", "ObjectModule": "ObjectModule.bsl",
    "МодульНабораЗаписей": "RecordSetModule.bsl", "RecordSetModule": "RecordSetModule.bsl",
    "МодульМенеджераЗначения": "ValueManagerModule.bsl", "ValueManagerModule": "ValueManagerModule.bsl",
}


@dataclass(frozen=True)
class DiagnosticsTarget:
    """Что отдать пакетному `analyze`: каталог целиком (files is None) или только
    перечисленные файлы. Второе — объект в EDT: его модули лежат рядом с Forms/ и
    Commands/, и каталог объекта целиком потащил бы в проверку все формы."""
    src_dir: Path
    files: tuple[Path, ...] | None = None


def diagnostics_target(workspace: Path, full_name: str) -> DiagnosticsTarget | None:
    """Цель пакетного `analyze` по имени модуля:
      'ОбщийМодуль.X'                         -> модуль общего модуля;
      'Справочник.X'                          -> модули объекта (без форм и команд);
      'Справочник.X.Форма.ИмяФормы'           -> каталог модуля формы;
      'Справочник.X.Команда.ИмяКоманды'       -> каталог модуля команды.
    Форма/команда — по МАРКЕРУ на 3-й позиции: принимаем и каноничное
    'Тип.Объект.Форма.Имя' (ПолноеИмя), и 5-частное 'Тип.Объект.Форма.Имя.Форма'."""
    parts = full_name.split(".")
    if len(parts) < 2:
        return None
    base = _object_base(workspace, parts[0], parts[1])
    if base is None:
        return None
    layout = source_layout.detect(workspace)
    if len(parts) >= 4 and valid_segment(parts[3]) and parts[2] in _FORM_MARK | _CMD_MARK:
        d = (source_layout.form_dir(layout, base, parts[3]) if parts[2] in _FORM_MARK
             else source_layout.command_dir(layout, base, parts[3]))
        return DiagnosticsTarget(d) if d.exists() else None
    own = source_layout.own_dir(layout, base)
    if not own.exists():
        return None
    if layout == source_layout.DESIGNER:
        return DiagnosticsTarget(own)          # Ext/ — только модули объекта (и Form/ общей формы)
    files = tuple(sorted(p for p in own.glob("*.bsl") if p.is_file()))
    return DiagnosticsTarget(own, files) if files else None


def diagnostics_src_dir(workspace: Path, full_name: str) -> Path | None:
    """Каталог цели `analyze` (совместимость; полная цель — diagnostics_target)."""
    target = diagnostics_target(workspace, full_name)
    return target.src_dir if target else None


def _form_module(workspace: Path, base: Path, form: str) -> Path:
    return source_layout.form_dir(source_layout.detect(workspace), base, form) / "Module.bsl"


def _command_module(workspace: Path, base: Path, command: str) -> Path:
    return source_layout.command_dir(source_layout.detect(workspace), base, command) / "CommandModule.bsl"


def _kind_module(workspace: Path, base: Path, kind: str) -> Path:
    return source_layout.own_dir(source_layout.detect(workspace), base) / _KIND_FILE[kind]


def symbol_candidates(workspace: Path, full_name: str) -> tuple[list[str], str]:
    """Имя 1С -> (файлы-кандидаты для documentSymbol, имя символа). Понимает:
      Тип.Модуль.Символ                          — общий модуль / модуль объекта;
      Тип.Объект.Форма.ИмяФормы.Символ           — модуль управляемой формы;
      Тип.Объект.Команда.ИмяКоманды.Символ       — модуль команды;
      Тип.Объект.МодульМенеджера.Символ (и пр.)  — конкретный модуль объекта.
    Форма/команда/модуль распознаются по МАРКЕРУ на 3-й позиции (как diagnostics_target),
    поэтому адрес из ответа инструмента валиден и на входе (round-trip). Список пуст —
    тип/файл не найден; символ всё равно возвращаем для сообщения об ошибке."""
    parts = full_name.split(".")
    if len(parts) < 3:
        raise ValueError(f"ожидался формат Тип.Модуль.Символ, получено: {full_name!r}")
    base = _object_base(workspace, parts[0], parts[1])
    if base is None:
        return [], ".".join(parts[2:])

    def _one(fpath: Path, symbol: str) -> tuple[list[str], str]:
        return ([fpath.resolve().as_uri()] if fpath.exists() else []), symbol

    if len(parts) >= 5 and parts[2] in _FORM_MARK and valid_segment(parts[3]):
        return _one(_form_module(workspace, base, parts[3]), ".".join(parts[4:]))
    if len(parts) >= 5 and parts[2] in _CMD_MARK and valid_segment(parts[3]):
        return _one(_command_module(workspace, base, parts[3]), ".".join(parts[4:]))
    if len(parts) >= 4 and parts[2] in _KIND_FILE:
        return _one(_kind_module(workspace, base, parts[2]), ".".join(parts[3:]))
    # обычный Тип.Модуль.Символ — общий модуль или модуль объекта (перебор кандидатов)
    return candidate_uris(workspace, parts[0], parts[1]), ".".join(parts[2:])


def module_file_uri(workspace: Path, module_full_name: str) -> str | None:
    """URI ФАЙЛА модуля по его АДРЕСУ (без символа) — для сложности/линз. Формы и
    команды распознаются так же, как в symbol_candidates:
      'ОбщийМодуль.X'              -> модуль общего модуля
      'Справочник.X'               -> первый существующий модуль объекта (module_uri)
      'Справочник.X.Форма.Имя'     -> модуль формы
      'Справочник.X.МодульОбъекта' -> модуль объекта"""
    parts = module_full_name.split(".")
    if len(parts) < 2:
        return None
    base = _object_base(workspace, parts[0], parts[1])
    if base is None:
        return None

    def _u(p: Path) -> str | None:
        return p.resolve().as_uri() if p.exists() else None

    if len(parts) >= 4 and parts[2] in _FORM_MARK and valid_segment(parts[3]):
        return _u(_form_module(workspace, base, parts[3]))
    if len(parts) >= 4 and parts[2] in _CMD_MARK and valid_segment(parts[3]):
        return _u(_command_module(workspace, base, parts[3]))
    if len(parts) >= 3 and parts[2] in _KIND_FILE:
        return _u(_kind_module(workspace, base, parts[2]))
    return module_uri(workspace, parts[0], parts[1])


def split_full_name(full_name: str) -> tuple[str, str, str]:
    """'ОбщийМодуль.МойМодуль.ИмяФункции' -> ('ОбщийМодуль', 'МойМодуль', 'ИмяФункции').
    (Оставлен для совместимости; символьный путь использует symbol_candidates.)"""
    parts = full_name.split(".")
    if len(parts) < 3:
        raise ValueError(f"ожидался формат Тип.Модуль.Символ, получено: {full_name!r}")
    return parts[0], parts[1], ".".join(parts[2:])
