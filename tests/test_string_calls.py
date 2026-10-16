"""Вызовы по строковому имени (ЗИ-34): разбор литералов, поиск кандидатов, слияние с графом."""
import asyncio
import os
from pathlib import Path

from checks_support import commit, git, write
from test_tools_mock import FakeAnalyzer, FakeLsp, _settings

from bsl_ls_mcp.application import tools as T
from bsl_ls_mcp.domain.string_calls import find_string_calls, literal_names, string_literals
from bsl_ls_mcp.infrastructure.text_search import WorkspaceTextSearch

MODULE = [
    "Процедура ЗапуститьФон() Экспорт",
    '\tДлительныеОперации.ВыполнитьФункцию(Параметры, "фск_Казначейство.СоздатьРеестры", А);',
    "КонецПроцедуры",
    "",
    "Функция ИмяМетода()",
    '\tВыполняемыйМетод = "ФСК_КАЗНАЧЕЙСТВО.создатьреестры";   // регистр не важен',
    "\tВозврат ВыполняемыйМетод;",
    "КонецФункции",
    "",
    "Процедура НеВызовы()",
    '\t// ДлительныеОперации.ВыполнитьФункцию(П, "фск_Казначейство.СоздатьРеестры");',
    '\tТекст = "фск_Казначейство.СоздатьРеестры и ещё слова";',
    '\tЗапрос.Текст = "ВЫБРАТЬ',
    '\t|"фск_Казначейство.СоздатьРеестры"";',
    '\tЧужой = "фск_Казначейство.СоздатьРеестрыДругие";',
    "КонецПроцедуры",
    'ВТелеМодуля = "фск_Казначейство.СоздатьРеестры";',
]


def test_literal_names():
    assert literal_names("ОбщийМодуль.фск_Казначейство.СоздатьРеестры") == ["фск_Казначейство.СоздатьРеестры"]
    assert literal_names("Документ.Заказ.Провести") == ["Документы.Заказ.Провести"]
    assert literal_names("Справочник.Х.Форма.Ф.Метод") == []
    assert literal_names("ОбщийМодуль.М") == []


def test_string_literals_split_and_skip():
    assert string_literals('А = "раз" + "два ""в кавычках""" // "коммент"') == ["раз", 'два "в кавычках"']
    assert string_literals('// "закомментировано"') == []
    assert string_literals('\t|"строка запроса"') == []
    assert string_literals('Т = "начало многострочного') == []


def test_find_string_calls_with_enclosing_methods():
    calls = find_string_calls(MODULE, ["фск_Казначейство.СоздатьРеестры"])
    assert [(c.line, c.method, c.kind) for c in calls] == [
        (2, "ЗапуститьФон", "Процедура"), (6, "ИмяМетода", "Функция"), (17, None, None)]


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "develop")
    write(repo, "BF/src/CommonModules/Форма/Module.bsl", MODULE, eol="\n")
    write(repo, "BF/src/CommonModules/Пусто/Module.bsl", ["Процедура П()", "КонецПроцедуры"], eol="\n")
    write(repo, "Вне/Module.bsl", MODULE, eol="\n")
    commit(repo)
    return repo, repo / "BF" / "src"


def test_text_search_git_includes_untracked_and_stays_in_workspace(tmp_path):
    repo, ws = _repo(tmp_path)
    write(repo, "BF/src/CommonModules/Новый/Module.bsl",
          ['Процедура Н()', '\tИмя = "фск_Казначейство.СоздатьРеестры";', "КонецПроцедуры"], eol="\n")
    found = sorted(p.parent.name for p, _ in WorkspaceTextSearch(ws).files_with('СоздатьРеестры"'))
    assert found == ["Новый", "Форма"]


def test_text_search_without_git(tmp_path):
    ws = tmp_path / "src"
    for name, lines in (("А", MODULE), ("Б", ["Процедура П()", "КонецПроцедуры"])):
        p = ws / "CommonModules" / name / "Module.bsl"
        p.parent.mkdir(parents=True)
        p.write_text("\n".join(lines), encoding="utf-8")
    os.environ.pop("GIT_DIR", None)
    assert [p.parent.name for p, _ in WorkspaceTextSearch(ws).files_with('СоздатьРеестры"')] == ["А"]


class FakeSearch:
    def __init__(self, files):
        self.files = files
        self.fragments = []

    def files_with(self, fragment):
        self.fragments.append(fragment)
        return self.files


def test_callers_merge_graph_and_string_calls():
    path = Path(r"C:\x\src\Documents\бит_ПлатежнаяПозиция\Forms\АРМ\Module.bsl")
    search = FakeSearch([(path, "\n".join(MODULE))])
    deps = T.Deps(lsp=FakeLsp(), analyzer=FakeAnalyzer([]), settings=_settings(), search=search)
    r = asyncio.run(T.bsl_callers(deps, "ОбщийМодуль.ТестОбщийМодуль.ВычислитьСумму"))
    assert [x["способ"] for x in r] == ["вызов"]                      # имя не совпало — строк нет
    assert search.fragments == ['ВычислитьСумму"']

    class Named(FakeLsp):
        async def document_symbol(self, uri):
            return [{"name": "СоздатьРеестры", "selectionRange": {"start": {"line": 0, "character": 0}}}]

        async def incoming_calls(self, item):
            return []                                                   # граф ничего не видит
    deps = T.Deps(lsp=Named(), analyzer=FakeAnalyzer([]), settings=_settings(), search=FakeSearch([(path, "\n".join(MODULE))]))
    r = asyncio.run(T.bsl_callers(deps, "ОбщийМодуль.ТестОбщийМодуль.СоздатьРеестры"))
    assert r == []                                                      # литерал другого модуля
    deps.search.files = [(path, "\n".join(m.replace("фск_Казначейство", "ТестОбщийМодуль") for m in MODULE))]
    r = asyncio.run(T.bsl_callers(deps, "ОбщийМодуль.ТестОбщийМодуль.СоздатьРеестры"))
    # строка 6 — «ФСК_КАЗНАЧЕЙСТВО…» прописными: замена её не тронула, литерал по-прежнему чужой
    assert [(x["full_name"], x["kind"], x["способ"], x["строки"]) for x in r] == [
        ("Документ.бит_ПлатежнаяПозиция.Форма.АРМ.<тело модуля>", "module", "строка", [17]),
        ("Документ.бит_ПлатежнаяПозиция.Форма.АРМ.ЗапуститьФон", "procedure", "строка", [2]),
    ]
