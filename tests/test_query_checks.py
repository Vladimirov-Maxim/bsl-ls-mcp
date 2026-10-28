"""Проверка пакетного запроса: временная таблица из ПОМЕСТИТЬ стоит в соединении, а индекса нет.
Текст запроса — из литералов модуля (с `|`, из нескольких функций) и из схемы компоновки."""
from __future__ import annotations

from checks_support import commit, hits, new_repo, write

from bsl_ls_mcp.application.rules_check import run_rules_check
from bsl_ls_mcp.domain.checks import query_text as qt
from bsl_ls_mcp.domain.checks.bsl_module import ModuleModel
from bsl_ls_mcp.infrastructure.git_changes import GitChangeSource

KEY = "query.temp-table-join-without-index"

BATCH = [
    "Функция ТекстСальдо()",                                   # 1
    '\tВозврат "ВЫБРАТЬ Т.Договор ПОМЕСТИТЬ ВтСальдо',         # 2
    "\t|ИЗ РегистрНакопления.Расчеты.Остатки КАК Т",           # 3
    '\t|;";',                                                   # 4
    "КонецФункции",                                             # 5
    "",                                                         # 6
    "Функция Текст()",                                          # 7
    '\tТекст = ТекстСальдо() + "',                              # 8
    "\t|ВЫБРАТЬ Д.Ссылка ПОМЕСТИТЬ ВтДоговоры ИЗ Справочник.Договоры КАК Д",  # 9
    "\t|ИНДЕКСИРОВАТЬ ПО Д.Ссылка;",                           # 10
    "\t|ВЫБРАТЬ * ПОМЕСТИТЬ ВтМелкая ИЗ Справочник.Валюты КАК В;",  # 11
    "\t|ВЫБРАТЬ * ИЗ ВтМелкая КАК М;",                          # 12 — без соединения: не находка
    "\t|ВЫБРАТЬ * ИЗ Документ.Заказ КАК З",                     # 13
    "\t|// ЛЕВОЕ СОЕДИНЕНИЕ ВтМелкая КАК Закомм ПО ИСТИНА",     # 14 — комментарий запроса
    "\t|ЛЕВОЕ СОЕДИНЕНИЕ втсальдо КАК С ПО С.Договор = З.Договор",  # 15
    '\t|ВНУТРЕННЕЕ СОЕДИНЕНИЕ ВтДоговоры КАК Д ПО Д.Ссылка = З.Договор";',  # 16
    "\tВозврат Текст;",                                         # 17
    "КонецФункции",                                             # 18
]


def test_stream_and_detection():
    stream = qt.module_stream(ModuleModel(BATCH).parts)
    found = qt.unindexed_joined_temp_tables(stream)
    assert [(t.name, t.put_line, t.join_lines) for t in found] == [("ВтСальдо", 2, (15,))]


def test_drop_and_redefinition_end_table_life():
    lines = ['Т = "ВЫБРАТЬ С.А ПОМЕСТИТЬ Вт ИЗ Справочник.С КАК С;', "|УНИЧТОЖИТЬ Вт;",
             '|ВЫБРАТЬ * ИЗ Т КАК Т ЛЕВОЕ СОЕДИНЕНИЕ Вт КАК В ПО ИСТИНА";',
             'Т2 = "SELECT C.A INTO Tmp FROM Catalog.C AS C INDEX BY A; SELECT * FROM T LEFT JOIN Tmp AS X ON TRUE";']
    assert qt.unindexed_joined_temp_tables(qt.module_stream(ModuleModel(lines).parts)) == []


def test_small_tables_are_skipped():
    lines = ['Т = "ВЫБРАТЬ &Дата КАК Д ПОМЕСТИТЬ ВтПараметры;',
             "|ВЫБРАТЬ РАЗЛИЧНЫЕ ПЕРВЫЕ 1 С.А ПОМЕСТИТЬ ВтОдна ИЗ Справочник.С КАК С;",
             '|ВЫБРАТЬ * ИЗ Т КАК Т ЛЕВОЕ СОЕДИНЕНИЕ ВтПараметры КАК П ПО ИСТИНА ЛЕВОЕ СОЕДИНЕНИЕ ВтОдна КАК О ПО ИСТИНА";']
    assert qt.unindexed_joined_temp_tables(qt.module_stream(ModuleModel(lines).parts)) == []


def test_dcs_stream_unescapes_query():
    lines = ["<dataSets>", "<query>ВЫБРАТЬ С.А ПОМЕСТИТЬ Вт ИЗ Справочник.С КАК С", ";", "ВЫБРАТЬ * ИЗ Т КАК Т",
             "ЛЕВОЕ СОЕДИНЕНИЕ Вт КАК В ПО В.А &gt; 0</query>", "<query>ВЫБРАТЬ 2</query>"]
    found = qt.unindexed_joined_temp_tables(qt.dcs_stream(lines))
    assert [(t.name, t.put_line, t.join_lines) for t in found] == [("Вт", 2, (5,))]
    assert qt.is_dcs("Reports/О/Templates/Схема/Template.dcs")
    assert qt.is_dcs("Reports/О/Templates/Схема/Ext/Template.xml", "<DataCompositionSchema/>")
    assert not qt.is_dcs("Reports/О/Templates/Макет/Ext/Template.xml", "<SpreadsheetDocument/>")


def _check(repo):
    return run_rules_check(GitChangeSource(repo, "HEAD"), None, only=[KEY])


def test_module_task_lines_only(tmp_path):
    repo = new_repo(tmp_path)
    path = "InformationRegisters/Расчеты/Ext/ManagerModule.bsl"
    write(repo, path, BATCH[:6] + ["Функция Старая()", '\tВозврат "ВЫБРАТЬ 1";', "КонецФункции"])
    commit(repo)
    assert hits(_check(repo), KEY) == []
    write(repo, path, BATCH)                                    # задача дописала соединение со старой Вт
    found = hits(_check(repo), KEY)
    assert [(f, n) for f, n, _ in found] == [(path, 15)] and "ВтСальдо (помещена в строке 2)" in found[0][2]


def test_new_module_reports_put_line(tmp_path):
    repo = new_repo(tmp_path)
    path = "CommonModules/Отчеты/Ext/Module.bsl"
    write(repo, path, BATCH)
    found = hits(_check(repo), KEY)
    assert [(f, n) for f, n, _ in found] == [(path, 2)] and "соединение: строка 15" in found[0][2]


def test_dcs_in_task(tmp_path):
    repo = new_repo(tmp_path)
    dcs = "Reports/Отчет/Templates/Схема/Template.dcs"
    other = "Reports/Отчет/Templates/Макет/Ext/Template.xml"
    write(repo, other, ["<SpreadsheetDocument/>"])
    commit(repo)
    write(repo, dcs, ["<DataCompositionSchema>", "<query>ВЫБРАТЬ С.А ПОМЕСТИТЬ Вт ИЗ Справочник.С КАК С;",
                      "ВЫБРАТЬ * ИЗ Т КАК Т ЛЕВОЕ СОЕДИНЕНИЕ Вт КАК В ПО ИСТИНА</query>", "</DataCompositionSchema>"])
    write(repo, other, ["<SpreadsheetDocument>ПОМЕСТИТЬ Вт; СОЕДИНЕНИЕ Вт</SpreadsheetDocument>"])
    r = run_rules_check(GitChangeSource(repo, "HEAD"), None)
    assert [(f, n) for f, n, _ in hits(r, KEY)] == [(dcs, 2)]
    assert {f["файл"] for f in r["находки"]} == {dcs}                # прочие проверки по XML не гоняются
