"""Мелкие исправления: пустая строка без отступа в паре каталогов, повторное чтение реквизита на строке задачи,
bsl_complexity по всем модулям объекта."""
import asyncio
import dataclasses

from checks_support import commit, hits, new_repo, registry, write
from test_tools_mock import FakeAnalyzer, FakeLsp, _settings

from bsl_ls_mcp.application import tools as T
from bsl_ls_mcp.application.rules_check import run_rules_check
from bsl_ls_mcp.infrastructure.dir_changes import DirPairChangeSource
from bsl_ls_mcp.infrastructure.git_changes import GitChangeSource

# Как в реальной задаче: после пустой строки вставлен блок, который сам кончается пустой строкой
# без отступа. Построчное сравнение выравнивало старую пустую на конец блока и называло её (2),
# git diff — добавленную (4). Пример найден перебором; на реальном файле — строки 64 и 73.
BEFORE = ["Процедура П()", "", "\tА = 1;", "\t// комментарий", "КонецПроцедуры"]
AFTER = ["Процедура П()", "", "\t\tВозврат;", "", "\tА = 1;", "\t// комментарий", "КонецПроцедуры"]


def test_dir_pair_bare_blank_line_points_to_added_line(tmp_path):
    base, target = tmp_path / "эталон", tmp_path / "копия"
    write(base, "Обр/Forms/Форма/Ext/Form/Module.bsl", BEFORE)
    write(target, "Обр/Forms/Форма/Ext/Form/Module.bsl", AFTER)
    r = run_rules_check(DirPairChangeSource(base, target), registry(), only=["layout.bare-blank-line"])
    assert [f["строка"] for f in r["находки"]] == [4]          # добавленная, а не старая (2)
    # без git — прежнее построчное сравнение (запасной путь): именно оно и ошибалось
    r = run_rules_check(DirPairChangeSource(base, target, git=None), registry(), only=["layout.bare-blank-line"])
    assert [f["строка"] for f in r["находки"]] == [2]


def test_repeated_attribute_reported_on_task_line(tmp_path):
    repo = new_repo(tmp_path)
    m = "CommonModules/Виз/Ext/Module.bsl"
    write(repo, m, ["Процедура П(Виза)", "\tX = 1;",
                    "\tА = ОбщегоНазначения.ЗначениеРеквизитаОбъекта(Виза, \"Статус\");",
                    "\tБ = ОбщегоНазначения.ЗначениеРеквизитаОбъекта(Виза, \"Дата\");", "КонецПроцедуры"])
    commit(repo)
    write(repo, m, ["Процедура П(Виза)", "\tX = ОбщегоНазначения.ЗначениеРеквизитаОбъекта(Виза, \"Автор\");",
                    "\tА = ОбщегоНазначения.ЗначениеРеквизитаОбъекта(Виза, \"Статус\");",
                    "\tБ = ОбщегоНазначения.ЗначениеРеквизитаОбъекта(Виза, \"Дата\");", "КонецПроцедуры"])
    found = hits(run_rules_check(GitChangeSource(repo, "HEAD"), registry()), "read.repeated-attribute-value")
    assert [(n, t.split(": ", 1)[1]) for _, n, t in found] == [
        (2, "Виза, повторные вызовы в существующем коде — строки 3, 4")]


def test_complexity_covers_all_object_modules(tmp_path):
    ws = tmp_path / "src"
    (ws / "Configuration").mkdir(parents=True)
    (ws / "Configuration" / "Configuration.mdo").write_text("<mdclass:Configuration/>", encoding="utf-8")
    obj = ws / "Reports" / "Реестр"
    obj.mkdir(parents=True)
    for name in ("ManagerModule.bsl", "ObjectModule.bsl"):
        (obj / name).write_text("Процедура П()\nКонецПроцедуры\n", encoding="utf-8")

    class Lenses(FakeLsp):
        async def code_lens(self, uri):
            method = "ПриКомпоновкеРезультата" if "ObjectModule" in uri else "ОпределитьНастройки"
            return [{"data": {"id": "CognitiveComplexity", "methodName": method}},
                    {"data": {"id": "CyclomaticComplexity", "methodName": method}}]

    s = dataclasses.replace(_settings(), workspace=ws, allowed_roots=(ws,))
    deps = T.Deps(lsp=Lenses(), analyzer=FakeAnalyzer([]), settings=s)
    r = asyncio.run(T.bsl_complexity(deps, "Отчет.Реестр"))
    assert sorted(x["full_name"] for x in r) == ["Отчет.Реестр.МодульМенеджера.ОпределитьНастройки",
                                                 "Отчет.Реестр.МодульОбъекта.ПриКомпоновкеРезультата"]
    r = asyncio.run(T.bsl_complexity(deps, "Отчет.Реестр", function="прикомпоновкерезультата"))
    assert [x["full_name"] for x in r] == ["Отчет.Реестр.МодульОбъекта.ПриКомпоновкеРезультата"]
    r = asyncio.run(T.bsl_complexity(deps, "Отчет.Реестр.МодульОбъекта"))
    assert [x["full_name"] for x in r] == ["Отчет.Реестр.МодульОбъекта.ПриКомпоновкеРезультата"]
