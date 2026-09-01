"""Резолв доменного имени 1С -> файлы-кандидаты модуля (детерминированно, по карте
каталогов CR-выгрузки). Точную ПОЗИЦИЮ символа спрашиваем у LSP-сервера
(documentSymbol) в application — без текстового угадывания по .bsl."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .one_c_config_path import _TYPE_EN_TO_DIR
from .one_c_naming import type_ru_to_en

# Имя объекта/модуля/формы 1С — идентификатор: буквы (лат./кир.), цифры, подчёркивание.
# НИКАКИХ / \ : . пробелов — иначе сегмент имени уводит путь наружу (обход каталога,
# абсолютная замена базы, UNC). Валидируем каждый сегмент перед подстановкой в путь.
_SAFE_SEGMENT = re.compile(r"^[0-9A-Za-z_Ѐ-ӿ]+$")


def valid_segment(name: str) -> bool:
    """Безопасный сегмент имени (без разделителей пути и точек)."""
    return bool(_SAFE_SEGMENT.match(name))


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

# Кандидаты bsl-модулей по типу объекта. Общий модуль — единственный Module.bsl;
# прикладной объект — символ может быть в модуле менеджера/объекта/набора записей.
_MODULE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "CommonModule": ("Ext/Module.bsl",),
    "_default": (
        "Ext/ManagerModule.bsl",
        "Ext/ObjectModule.bsl",
        "Ext/RecordSetModule.bsl",
        "Ext/ValueManagerModule.bsl",
    ),
}


@dataclass(frozen=True)
class Position:
    uri: str
    line: int       # 0-based (LSP)
    character: int


def _candidate_files(workspace: Path, type_en: str, module: str) -> list[Path]:
    directory = _TYPE_EN_TO_DIR.get(type_en)
    if directory is None or not valid_segment(module):
        return []
    rels = _MODULE_CANDIDATES.get(type_en, _MODULE_CANDIDATES["_default"])
    return [workspace / directory / module / rel for rel in rels]


def candidate_uris(workspace: Path, type_ru: str, module: str) -> list[str]:
    """URI существующих файлов-кандидатов модуля (для запроса documentSymbol).
    Пусто — неизвестный тип или нет файлов."""
    type_en = type_ru_to_en(type_ru)
    if type_en is None:
        return []
    return [p.resolve().as_uri() for p in _candidate_files(workspace, type_en, module) if p.exists()]


def module_uri(workspace: Path, type_ru: str, module: str) -> str | None:
    """URI файла модуля (первый существующий кандидat) — для диагностик/сложности.
    Содержимое читает клиент при синхронизации, здесь только адрес."""
    type_en = type_ru_to_en(type_ru)
    if type_en is None:
        return None
    for fpath in _candidate_files(workspace, type_en, module):
        if fpath.exists():
            return fpath.resolve().as_uri()
    return None


def diagnostics_src_dir(workspace: Path, full_name: str) -> Path | None:
    """Каталог для пакетного `analyze` по имени модуля. В отличие от module_uri
    (один файл для LSP), analyze берёт ПАПКУ и рекурсивно проверяет её .bsl:
      'ОбщийМодуль.X'                         -> CommonModules/X/Ext        (Module.bsl)
      'Справочник.X'                          -> Catalogs/X/Ext             (модули объекта, без форм)
      'Справочник.X.Форма.ИмяФормы.Форма'     -> Catalogs/X/Forms/ИмяФормы/Ext/Form
    Формы лежат в Forms/<имя>/Ext/Form/ — поэтому имя формы даёт точную папку формы,
    а не ManagerModule."""
    parts = full_name.split(".")
    if len(parts) < 2:
        return None
    type_en = type_ru_to_en(parts[0])
    directory = _TYPE_EN_TO_DIR.get(type_en) if type_en else None
    if directory is None or not valid_segment(parts[1]):
        return None
    base = workspace / directory / parts[1]
    # Форма (и команда) — по МАРКЕРУ на 3-й позиции, без завязки на хвост:
    # принимаем и каноничное 'Тип.Объект.Форма.Имя' (ПолноеИмя), и 5-частное
    # 'Тип.Объект.Форма.Имя.Форма'. Маркер задаёт подкаталог Ext.
    _SUBDIR = {"Форма": ("Forms", "Ext/Form"), "Form": ("Forms", "Ext/Form"),
               "Команда": ("Commands", "Ext"), "Command": ("Commands", "Ext")}
    if len(parts) >= 4 and parts[2] in _SUBDIR and valid_segment(parts[3]):
        folder, ext = _SUBDIR[parts[2]]
        d = base / folder / parts[3]
        for seg in ext.split("/"):
            d = d / seg
        return d if d.exists() else None
    # общий модуль / прикладной объект: его собственные модули в Ext (формы — отдельно)
    d = base / "Ext"
    return d if d.exists() else None


# Маркеры подмодулей на 3-й позиции имени (тем же правилом, что diagnostics_src_dir).
_FORM_MARK = {"Форма", "Form"}
_CMD_MARK = {"Команда", "Command"}
# Конкретный модуль прикладного объекта по русскому имени -> файл.
_KIND_FILE = {
    "МодульМенеджера": "ManagerModule.bsl", "ManagerModule": "ManagerModule.bsl",
    "МодульОбъекта": "ObjectModule.bsl", "ObjectModule": "ObjectModule.bsl",
    "МодульНабораЗаписей": "RecordSetModule.bsl", "RecordSetModule": "RecordSetModule.bsl",
    "МодульМенеджераЗначения": "ValueManagerModule.bsl", "ValueManagerModule": "ValueManagerModule.bsl",
}


def symbol_candidates(workspace: Path, full_name: str) -> tuple[list[str], str]:
    """Имя 1С -> (файлы-кандидаты для documentSymbol, имя символа). Понимает:
      Тип.Модуль.Символ                          — общий модуль / модуль объекта;
      Тип.Объект.Форма.ИмяФормы.Символ           — модуль управляемой формы;
      Тип.Объект.Команда.ИмяКоманды.Символ       — модуль команды;
      Тип.Объект.МодульМенеджера.Символ (и пр.)  — конкретный модуль объекта.
    Форма/команда/модуль распознаются по МАРКЕРУ на 3-й позиции (как diagnostics_src_dir),
    поэтому адрес из ответа инструмента валиден и на входе (round-trip). Список пуст —
    тип/файл не найден; символ всё равно возвращаем для сообщения об ошибке."""
    parts = full_name.split(".")
    if len(parts) < 3:
        raise ValueError(f"ожидался формат Тип.Модуль.Символ, получено: {full_name!r}")
    type_en = type_ru_to_en(parts[0])
    directory = _TYPE_EN_TO_DIR.get(type_en) if type_en else None
    if directory is None or not valid_segment(parts[1]):
        return [], ".".join(parts[2:])
    base = workspace / directory / parts[1]

    def _one(fpath: Path, symbol: str) -> tuple[list[str], str]:
        return ([fpath.resolve().as_uri()] if fpath.exists() else []), symbol

    if len(parts) >= 5 and parts[2] in _FORM_MARK and valid_segment(parts[3]):
        return _one(base / "Forms" / parts[3] / "Ext" / "Form" / "Module.bsl", ".".join(parts[4:]))
    if len(parts) >= 5 and parts[2] in _CMD_MARK and valid_segment(parts[3]):
        return _one(base / "Commands" / parts[3] / "Ext" / "CommandModule.bsl", ".".join(parts[4:]))
    if len(parts) >= 4 and parts[2] in _KIND_FILE:
        return _one(base / "Ext" / _KIND_FILE[parts[2]], ".".join(parts[3:]))
    # обычный Тип.Модуль.Символ — общий модуль или модуль объекта (перебор кандидатов)
    return candidate_uris(workspace, parts[0], parts[1]), ".".join(parts[2:])


def module_file_uri(workspace: Path, module_full_name: str) -> str | None:
    """URI ФАЙЛА модуля по его АДРЕСУ (без символа) — для сложности/линз. Формы и
    команды распознаются так же, как в symbol_candidates:
      'ОбщийМодуль.X'              -> CommonModules/X/Ext/Module.bsl
      'Справочник.X'               -> первый существующий модуль объекта (module_uri)
      'Справочник.X.Форма.Имя'     -> Catalogs/X/Forms/Имя/Ext/Form/Module.bsl
      'Справочник.X.МодульОбъекта' -> Catalogs/X/Ext/ObjectModule.bsl"""
    parts = module_full_name.split(".")
    if len(parts) < 2:
        return None
    type_en = type_ru_to_en(parts[0])
    directory = _TYPE_EN_TO_DIR.get(type_en) if type_en else None
    if directory is None or not valid_segment(parts[1]):
        return None
    base = workspace / directory / parts[1]

    def _u(p: Path) -> str | None:
        return p.resolve().as_uri() if p.exists() else None

    if len(parts) >= 4 and parts[2] in _FORM_MARK and valid_segment(parts[3]):
        return _u(base / "Forms" / parts[3] / "Ext" / "Form" / "Module.bsl")
    if len(parts) >= 4 and parts[2] in _CMD_MARK and valid_segment(parts[3]):
        return _u(base / "Commands" / parts[3] / "Ext" / "CommandModule.bsl")
    if len(parts) >= 3 and parts[2] in _KIND_FILE:
        return _u(base / "Ext" / _KIND_FILE[parts[2]])
    return module_uri(workspace, parts[0], parts[1])


def split_full_name(full_name: str) -> tuple[str, str, str]:
    """'ОбщийМодуль.МойМодуль.ИмяФункции' -> ('ОбщийМодуль', 'МойМодуль', 'ИмяФункции').
    (Оставлен для совместимости; символьный путь использует symbol_candidates.)"""
    parts = full_name.split(".")
    if len(parts) < 3:
        raise ValueError(f"ожидался формат Тип.Модуль.Символ, получено: {full_name!r}")
    return parts[0], parts[1], ".".join(parts[2:])
