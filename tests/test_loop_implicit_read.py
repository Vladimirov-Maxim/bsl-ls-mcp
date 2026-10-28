"""`loop.db-read`: неявное чтение в цикле — представление (`Строка`, `СтрШаблон`, конкатенация) или
реквизит через точку у ссылочного поля выборки запроса; `ПолучитьОбъект()` под запись — не находка."""
from __future__ import annotations

from checks_support import commit, hits, new_repo, write

from bsl_ls_mcp.application.rules_check import run_rules_check
from bsl_ls_mcp.domain.checks import query_text as qt
from bsl_ls_mcp.domain.checks.bsl_module import ModuleModel
from bsl_ls_mcp.infrastructure.git_changes import GitChangeSource

KEY = "loop.db-read"
PATH = "CommonModules/Платежи/Ext/Module.bsl"

MODULE = [
    "Функция Доступность(Позиции) Экспорт",                                     # 1
    "\tЗапрос = Новый Запрос(ТекстЗапроса());",                                 # 2
    "\tВыборка = Запрос.Выполнить().Выбрать();",                                # 3
    "\tПока Выборка.Следующий() Цикл",                                          # 4
    "\t\tТекст = Причина(Строка(Выборка.Позиция), Выборка.Сумма);",             # 5
    "\t\tТекст = СтрШаблон(\"%1 %2\",",                                         # 6
    "\t\t\tВыборка.ПозицияПредставление, Выборка.Реестр);",                     # 7
    "\t\tТекст = \"Позиция \" + Выборка.Позиция;",                              # 8
    "\t\tСумма = Выборка.Реестр.СуммаДокумента;",                               # 9
    "\t\tЕсли ЗначениеЗаполнено(Выборка.Реестр) Тогда",                         # 10
    "\t\t\tТекст = Строка(Выборка.Позиция.УникальныйИдентификатор()) + Выборка.Комментарий;",  # 11
    "\t\tКонецЕсли;",                                                           # 12
    "\tКонецЦикла;",                                                            # 13
    "\tТаблица = Запрос.Выполнить().Выгрузить();",                              # 14
    "\tДля Каждого Стр Из Таблица Цикл",                                        # 15
    "\t\tТекст = Строка(Стр.Позиция);",                                         # 16
    "\tКонецЦикла;",                                                            # 17
    "\tДля Каждого Позиция Из Позиции Цикл",                                    # 18
    "\t\tТекст = Строка(Позиция.Позиция);",                                     # 19 — не выборка запроса
    "\tКонецЦикла;",                                                            # 20
    "\tВозврат Текст;",                                                         # 21
    "КонецФункции",                                                             # 22
    "",                                                                         # 23
    "Функция ТекстЗапроса()",                                                   # 24
    "\tВозврат \"ВЫБРАТЬ П.Ссылка КАК Позиция, П.Сумма КАК Сумма, П.Комментарий КАК Комментарий,",  # 25
    "\t|ПРЕДСТАВЛЕНИЕ(П.Ссылка) КАК ПозицияПредставление, ПРЕДСТАВЛЕНИЕ(П.Реестр) КАК РеестрПредставление,",
    "\t|П.Реестр",                                                              # 27
    "\t|ИЗ Документ.ПлатежнаяПозиция КАК П\";",                                 # 28
    "КонецФункции",                                                             # 29
]


def _check(repo):
    return run_rules_check(GitChangeSource(repo, "HEAD"), None, only=[KEY])


def test_reference_fields_from_query_text():
    refs = qt.reference_fields(qt.module_stream(ModuleModel(MODULE).parts))
    assert {"позиция", "реестр", "ссылка"} <= refs
    assert not {"сумма", "комментарий", "позицияпредставление"} & refs


def test_implicit_reads_in_loop(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, PATH, MODULE)
    found = {n: text for _, n, text in hits(_check(repo), KEY)}
    assert sorted(found) == [5, 7, 8, 9, 16]
    assert found[5].endswith("неявно: Строка(Выборка.Позиция)")
    assert "Выборка.Реестр в СтрШаблон" in found[7]           # аргумент многострочного СтрШаблон
    assert "в конкатенации" in found[8]
    assert "Выборка.Реестр.СуммаДокумента — реквизит ссылки" in found[9]
    assert "Строка(Стр.Позиция)" in found[16]


def test_old_lines_are_not_reported(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, PATH, MODULE)
    commit(repo)
    write(repo, PATH, MODULE[:9] + ["\t\tСумма = Выборка.Сумма;"] + MODULE[9:])
    assert hits(_check(repo), KEY) == []


def test_get_object_for_write_is_not_a_read(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, PATH, [
        "Процедура Обработать(Ссылки) Экспорт",                 # 1
        "\tДля Каждого Ссылка Из Ссылки Цикл",                  # 2
        "\t\tОбъект = Ссылка.ПолучитьОбъект();",                # 3 — дальше записывается
        "\t\tОбъект.Комментарий = \"\";",                       # 4
        "\t\tЕсли Объект.Проверить() Тогда",                    # 5
        "\t\t\tОбъект.Записать();",                             # 6
        "\t\tКонецЕсли;",                                       # 7
        "\tКонецЦикла;",                                        # 8
        "\tДля Каждого Ссылка Из Ссылки Цикл",                  # 9
        "\t\tДанные = Ссылка.ПолучитьОбъект();",                # 10 — только читается
        "\t\tСумма = Данные.Сумма;",                            # 11
        "\tКонецЦикла;",                                        # 12
        "\tДанные.Записать();",                                 # 13 — после цикла: не в том же цикле
        "КонецПроцедуры",                                       # 14
    ])
    assert [n for _, n, _ in hits(_check(repo), KEY)] == [10]
