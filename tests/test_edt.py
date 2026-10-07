"""Проект EDT наряду с выгрузкой конфигуратора: раскладка модулей, резолв имён,
цель диагностик, подписи и проверки правок задачи в репозитории, где конфигурация
лежит не в корне (`BF/src/`). Без java: анализатор подменён."""
from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest
from checks_support import PREFIX, commit, git, hits, registry, write

from bsl_ls_mcp.application import tools
from bsl_ls_mcp.application.rules_check import run_rules_check, run_task_methods
from bsl_ls_mcp.domain import mapper, resolver, source_layout
from bsl_ls_mcp.domain.checks.engine import own_modules
from bsl_ls_mcp.domain.checks.module_names import common_module_paths, module_label
from bsl_ls_mcp.infrastructure.git_changes import GitChangeSource, config_prefixes
from bsl_ls_mcp.settings import get_settings

EDT_FILES = {
    "Configuration/Configuration.mdo": "<mdclass:Configuration/>",
    "Configuration/SessionModule.bsl": "Процедура УстановкаПараметровСеанса(Имена)\nКонецПроцедуры\n",
    "CommonModules/ОбщийЕДТ/ОбщийЕДТ.mdo": "<mdclass:CommonModule/>",
    "CommonModules/ОбщийЕДТ/Module.bsl": "Функция Ф() Экспорт\n\tВозврат 1;\nКонецФункции\n",
    "Catalogs/Спр/Спр.mdo": "<mdclass:Catalog/>",
    "Catalogs/Спр/ManagerModule.bsl": "Процедура М() Экспорт\nКонецПроцедуры\n",
    "Catalogs/Спр/ObjectModule.bsl": "Процедура О() Экспорт\nКонецПроцедуры\n",
    "Catalogs/Спр/Forms/ФормаЭлемента/Form.form": "<form:Form/>",
    "Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl": "&НаКлиенте\nПроцедура П()\nКонецПроцедуры\n",
    "Catalogs/Спр/Commands/Команда/CommandModule.bsl": "&НаКлиенте\nПроцедура ОбработкаКоманды(П, ПВ)\nКонецПроцедуры\n",
    "CommonForms/ОбщФорма/Module.bsl": "&НаКлиенте\nПроцедура П()\nКонецПроцедуры\n",
    "CommonCommands/ОбщКоманда/CommandModule.bsl": "&НаКлиенте\nПроцедура ОбработкаКоманды(П, ПВ)\nКонецПроцедуры\n",
}


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    for rel, text in EDT_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")      # EDT: без BOM, LF
    return root


def _uri(ws: Path, rel: str) -> str:
    return (ws / rel).resolve().as_uri()


# ---------- раскладка и резолв ----------
def test_layout_detected_by_root_marker(ws, tmp_path):
    assert resolver.layout_of(ws) == source_layout.EDT
    designer = tmp_path / "cf"
    (designer / "CommonModules" / "Х" / "Ext").mkdir(parents=True)   # без Configuration.xml — по модулю
    assert resolver.layout_of(designer) == source_layout.DESIGNER


def test_candidates_and_module_files(ws):
    assert resolver.candidate_uris(ws, "ОбщийМодуль", "ОбщийЕДТ") == [_uri(ws, "CommonModules/ОбщийЕДТ/Module.bsl")]
    assert resolver.candidate_uris(ws, "Справочник", "Спр") == [
        _uri(ws, "Catalogs/Спр/ManagerModule.bsl"), _uri(ws, "Catalogs/Спр/ObjectModule.bsl")]
    assert resolver.module_file_uri(ws, "Справочник.Спр.Форма.ФормаЭлемента") == \
        _uri(ws, "Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl")
    assert resolver.module_file_uri(ws, "Справочник.Спр.Команда.Команда") == \
        _uri(ws, "Catalogs/Спр/Commands/Команда/CommandModule.bsl")
    assert resolver.module_file_uri(ws, "Справочник.Спр.МодульОбъекта") == _uri(ws, "Catalogs/Спр/ObjectModule.bsl")
    assert resolver.module_file_uri(ws, "ОбщаяФорма.ОбщФорма") == _uri(ws, "CommonForms/ОбщФорма/Module.bsl")
    assert resolver.module_file_uri(ws, "ОбщаяКоманда.ОбщКоманда") == \
        _uri(ws, "CommonCommands/ОбщКоманда/CommandModule.bsl")


def test_symbol_candidates(ws):
    assert resolver.symbol_candidates(ws, "Справочник.Спр.Форма.ФормаЭлемента.П") == (
        [_uri(ws, "Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl")], "П")
    assert resolver.symbol_candidates(ws, "Справочник.Спр.МодульМенеджера.М") == (
        [_uri(ws, "Catalogs/Спр/ManagerModule.bsl")], "М")
    assert resolver.symbol_candidates(ws, "ОбщийМодуль.ОбщийЕДТ.Ф")[0] == [_uri(ws, "CommonModules/ОбщийЕДТ/Module.bsl")]


def test_diagnostics_target_object_excludes_forms(ws):
    t = resolver.diagnostics_target(ws, "Справочник.Спр")
    assert t.src_dir == ws / "Catalogs" / "Спр"
    assert [p.name for p in t.files] == ["ManagerModule.bsl", "ObjectModule.bsl"]
    form = resolver.diagnostics_target(ws, "Справочник.Спр.Форма.ФормаЭлемента")
    assert (form.src_dir, form.files) == (ws / "Catalogs" / "Спр" / "Forms" / "ФормаЭлемента", None)
    assert resolver.diagnostics_target(ws, "Справочник.Спр.Форма.Нет") is None
    assert resolver.diagnostics_target(ws, "ОбщийМодуль.ОбщийЕДТ").files == (
        ws / "CommonModules" / "ОбщийЕДТ" / "Module.bsl",)


def test_labels_from_edt_paths(ws):
    label = mapper.analyze_label_from_uri
    assert label(ws, _uri(ws, "Catalogs/Спр/ObjectModule.bsl")) == "Справочник.Спр.МодульОбъекта"
    assert label(ws, _uri(ws, "Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl")) == "Справочник.Спр.Форма.ФормаЭлемента"
    assert label(ws, _uri(ws, "CommonModules/ОбщийЕДТ/Module.bsl")) == "ОбщийМодуль.ОбщийЕДТ"
    assert label(ws, _uri(ws, "CommonForms/ОбщФорма/Module.bsl")) == "ОбщаяФорма.ОбщФорма"
    loc = mapper.location_to_code_location({"uri": _uri(ws, "Catalogs/Спр/Commands/Команда/CommandModule.bsl"),
                                            "range": {"start": {"line": 1}}})
    assert (loc.full_name, loc.text) == ("Справочник.Спр.Команда.Команда", "Процедура ОбработкаКоманды(П, ПВ)")


# ---------- bsl_diagnostics по имени: объект EDT — только свои модули ----------
def test_diagnostics_object_mirror_maps_back(ws):
    seen: list[list[str]] = []

    class FakeAnalyzer:
        async def analyze(self, src_dir):
            files = sorted(p.relative_to(src_dir).as_posix() for p in Path(src_dir).rglob("*.bsl"))
            seen.append(files)
            obj = Path(src_dir) / "Catalogs" / "Спр" / "ObjectModule.bsl"
            return [{"_src_uri": obj.resolve().as_uri(), "severity": "Error", "code": "X",
                     "message": "m", "range": {"start": {"line": 0}}}]

    s = dataclasses.replace(get_settings(), workspace=ws, allowed_roots=(ws,))
    deps = tools.Deps(lsp=None, analyzer=FakeAnalyzer(), settings=s)
    r = asyncio.run(tools.bsl_diagnostics(deps, "Справочник.Спр"))
    assert seen == [["Catalogs/Спр/ManagerModule.bsl", "Catalogs/Спр/ObjectModule.bsl"]]   # форм нет
    assert [(d["file"], d["severity"]) for d in r["diagnostics"]] == [("Справочник.Спр.МодульОбъекта", "error")]


# ---------- проверки правок задачи: репозиторий EDT ----------
def _edt_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "bf_edt"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "develop")
    src = "BF/src/"
    write(repo, src + "Configuration/Configuration.mdo", ["<mdclass:Configuration/>"], eol="\n")
    write(repo, src + "CommonModules/Вендорский/Вендорский.mdo", ["<mdclass:CommonModule/>"], eol="\n")
    write(repo, src + "CommonModules/Вендорский/Module.bsl", [
        "#Область ПрограммныйИнтерфейс", "Функция Публичный() Экспорт", "\tВозврат 1;", "КонецФункции",
        "#КонецОбласти", "#Область СлужебныйПрограммныйИнтерфейс", "Функция Служебный() Экспорт",
        "\tВозврат 2;", "КонецФункции", "#КонецОбласти"], eol="\n")
    commit(repo)
    return repo


def test_config_prefixes(tmp_path):
    repo = _edt_repo(tmp_path)
    assert config_prefixes(repo) == ("BF/src/",)
    plain = tmp_path / "plain"
    (plain / "x").mkdir(parents=True)
    assert config_prefixes(plain) == ("",)


def test_names_under_prefix():
    assert module_label("BF/src/Documents/Заказ/ObjectModule.bsl") == "Документ.Заказ.МодульОбъекта"
    assert module_label("BF/src/Catalogs/Спр/Forms/Ф/Module.bsl") == "Справочник.Спр.Форма.Ф"
    assert module_label("BF/src/CommonModules/М/Module.bsl") == "ОбщийМодуль.М"
    assert common_module_paths("М", ("BF/src/",)) == [
        "BF/src/CommonModules/М/Ext/Module.bsl", "BF/src/CommonModules/М/Module.bsl"]
    assert own_modules([("A", "BF/src/CommonModules/Новый/Новый.mdo"), ("A", "CommonModules/Старый.xml"),
                        ("A", "BF/src/CommonModules/Новый/Module.bsl"), ("M", "CommonModules/Б/Б.mdo")]) == \
        frozenset({"новый", "старый"})


def test_rules_check_in_edt_repo(tmp_path):
    repo = _edt_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "feature/T-1")
    src = "BF/src/"
    write(repo, src + "CommonModules/СвойНовый/СвойНовый.mdo", ["<mdclass:CommonModule/>"], eol="\n")
    write(repo, src + "CommonModules/СвойНовый/Module.bsl", [
        "#Область СлужебныйПрограммныйИнтерфейс", "Функция Своя() Экспорт", "\tВозврат 3;", "КонецФункции",
        "#КонецОбласти"], eol="\n")
    write(repo, src + f"CommonModules/{PREFIX}Задачи/{PREFIX}Задачи.mdo", ["<mdclass:CommonModule/>"], eol="\n")
    write(repo, src + f"CommonModules/{PREFIX}Задачи/Module.bsl", [
        "Функция Посчитать() Экспорт",
        "\tА = Вендорский.Служебный();",
        "\tБ = Вендорский.Публичный();",
        "\tВ = СвойНовый.Своя();",
        "\tВозврат А + Б + В;",
        "КонецФункции"], eol="\n")
    commit(repo, "первый коммит задачи")          # правки задачи уже в истории ветки
    source = GitChangeSource(repo)                 # база не задана → merge-base с develop
    assert source.base_info["как"] == "merge-base с develop"
    r = run_rules_check(source, registry())
    found = hits(r, "call.foreign-internal-api")
    assert [(f, n) for f, n, _ in found] == [(src + f"CommonModules/{PREFIX}Задачи/Module.bsl", 2)]
    assert r["база"]["ревизия"] == git(repo, "rev-parse", "develop").strip()
    methods = run_task_methods(GitChangeSource(repo))
    assert {(m["модуль"], m["метод"], m["статус"]) for m in methods} >= {
        (f"ОбщийМодуль.{PREFIX}Задачи", "Посчитать", "новый")}
    # прежнее поведение явной базой: от HEAD закоммиченных правок нет
    assert run_rules_check(GitChangeSource(repo, "HEAD"), registry())["находки"] == []


def test_base_falls_back_to_head_without_develop(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "Configuration.xml", ["<MetaDataObject/>"])
    commit(repo)
    src = GitChangeSource(repo)
    assert (src.base, src.config_prefixes) == ("HEAD", ("",))
    assert GitChangeSource(repo, base_branch="").base == "HEAD"
