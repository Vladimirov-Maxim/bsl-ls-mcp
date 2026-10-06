"""Кейсы проверок кода задачи на настоящем git-репозитории выгрузки.

Перенесены из фикстур конвейера (раздел «устройство кода», служебный интерфейс
чужих модулей, исключения реестра) — каждый и «ловит», и «не даёт ложного»;
имена объектов и префикс заменены нейтральными. Дополнены кейсами, которых там не
было: выходной параметр, пустые строки без отступа, режим ревизий, пара каталогов,
фильтры only/paths, методы задачи."""
from __future__ import annotations

from pathlib import Path

from checks_support import PREFIX, commit, git, hits, new_repo, registry, write

from bsl_ls_mcp.application.rules_check import InputError, run_rules_check, run_task_methods, source_kind
from bsl_ls_mcp.infrastructure.dir_changes import DirPairChangeSource
from bsl_ls_mcp.infrastructure.git_changes import GitChangeSource

STRUCT_BAD = [
    "Процедура Пересчитать(Договор) Экспорт",
    "\tНачатьТранзакцию();",
    "\tПопытка",
    "\t\tЗаписать(Договор);",
    "\t\tЗафиксироватьТранзакцию();",
    "\tИсключение",
    "\t\tЗаписатьОшибку(Договор);",
    "\tКонецПопытки;",
    "КонецПроцедуры",
    "",
    "Процедура Разнести(Таблица) Экспорт",
    "\tДля Каждого Стр Из Таблица Цикл",
    "\t\tОрг = ОбщегоНазначения.ЗначениеРеквизитаОбъекта(Стр.Договор, \"Организация\");",
    "\t\tДобавитьСтроку(Стр);",
    "\t\tНабор.Записать();",
    "\tКонецЦикла;",
    "КонецПроцедуры",
    "",
    "Процедура ДобавитьСтроку(Стр)",
    "\tЗаполнитьРеквизиты(Стр);",
    "КонецПроцедуры",
    "",
    "Процедура ЗаполнитьРеквизиты(Стр)",
    "\tСтр.Организация = ОбщегоНазначения.ЗначениеРеквизитаОбъекта(Стр.Договор, \"Организация\");",
    "\tСтр.Контрагент = ОбщегоНазначения.ЗначениеРеквизитаОбъекта(Стр.Договор, \"Владелец\");",
    "КонецПроцедуры",
    "",
    "Процедура Много(А, Б, В, Г, Д, Е)",
    "КонецПроцедуры",
    "",
    "Процедура Порядок(А = 1, Б)",
    "КонецПроцедуры",
    "",
    "Процедура Разрыв() Экспорт",
    "\tНачатьТранзакцию();",
    "\tБлокировка.Заблокировать();",
    "\tПопытка",
    "\t\tЗафиксироватьТранзакцию();",
    "\tИсключение",
    "\t\tОтменитьТранзакцию();",
    "\t\tВызватьИсключение;",
    "\tКонецПопытки;",
    "КонецПроцедуры",
]


def check(repo: Path, reg: dict | None = None, **kw) -> dict:
    return run_rules_check(GitChangeSource(repo, kw.pop("base", "HEAD"), kw.pop("rev", None)),
                           registry() if reg is None else reg, **kw)


def test_structure_violations_are_caught(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, f"CommonModules/{PREFIX}Плохой/Ext/Module.bsl", STRUCT_BAD)
    r = check(repo)
    f = f"CommonModules/{PREFIX}Плохой/Ext/Module.bsl"
    assert [h[:2] for h in hits(r, "tx.no-rollback")] == [(f, 6)]
    assert [h[:2] for h in hits(r, "loop.db-read")] == [(f, 13)]
    via = hits(r, "loop.db-read-via-call")
    assert [h[:2] for h in via] == [(f, 14)]
    assert "ДобавитьСтроку" in via[0][2] and "через ЗаполнитьРеквизиты" in via[0][2]
    assert [h[:2] for h in hits(r, "loop.db-write")] == [(f, 15)]
    rep = hits(r, "read.repeated-attribute-value")
    assert [h[:2] for h in rep] == [(f, 25)] and "Стр.Договор" in rep[0][2]
    assert [h[1] for h in hits(r, "params.too-many")] == [28] and "Много: 6" in hits(r, "params.too-many")[0][2]
    assert [h[1] for h in hits(r, "params.optional-before-required")] == [31]
    assert [h[:2] for h in hits(r, "tx.lock-outside-try")] == [(f, 36)]
    # «Записать(Договор)» без точки — вызов метода модуля, не запись в базу
    assert not [h for h in hits(r, "loop.db-write") if h[1] == 4]


def test_correct_transaction_and_clean_loops_are_silent(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, f"CommonModules/{PREFIX}Хороший/Ext/Module.bsl", [
        "Процедура Пересчитать(Договор) Экспорт",
        "\t",
        "\tНачатьТранзакцию();",
        "\tПопытка",
        "\t\tБлокировка = Новый БлокировкаДанных;",
        "\t\tБлокировка.Заблокировать();",
        "\t\tВыборка = Запрос.Выполнить().Выбрать();",
        "\t\tЗафиксироватьТранзакцию();",
        "\tИсключение",
        "\t\tОтменитьТранзакцию();",
        "\t\tЗаписьЖурналаРегистрации(\"Подсистема.Пересчет\", УровеньЖурналаРегистрации.Ошибка);",
        "\t\tВызватьИсключение;",
        "\tКонецПопытки;",
        "\t",
        "КонецПроцедуры",
        "",
        "Процедура Разнести(Таблица, Реквизиты) Экспорт",
        "\tЗначения = ОбщегоНазначения.ЗначенияРеквизитовОбъекта(Реквизиты.Договор, \"Организация, Владелец\");",
        "\tДля Каждого СтрокаТаблицы Из Таблица Цикл",
        "\t\tЗаполнить(СтрокаТаблицы, Значения);",
        "\tКонецЦикла;",
        "\tПока Выборка.Следующий() Цикл",
        "\tКонецЦикла;",
        "КонецПроцедуры",
        "",
        "Процедура Заполнить(СтрокаТаблицы, Значения)",
        "\tЗаполнитьЗначенияСвойств(СтрокаТаблицы, Значения);",
        "КонецПроцедуры",
        "",
        "Процедура Пять(А, Б, В, Г, Д = Неопределено)",
        "КонецПроцедуры",
    ])
    assert check(repo)["находки"] == []


def test_write_handler_without_exchange_check_and_own_transaction(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, f"Documents/{PREFIX}Наш/Ext/ObjectModule.bsl", [
        "Процедура ОбработкаПроведения(Отказ, РежимПроведения)", "\tЗначение = 1;", "КонецПроцедуры",
        "",
        "Процедура ПередЗаписью(Отказ, РежимЗаписи, РежимПроведения)",
        "\tНачатьТранзакцию();",
        "\tПопытка",
        "\t\tЗафиксироватьТранзакцию();",
        "\tИсключение",
        "\t\tОтменитьТранзакцию();",
        "\t\tВызватьИсключение;",
        "\tКонецПопытки;",
        "КонецПроцедуры",
    ])
    r = check(repo)
    assert [(h[1], "ПередЗаписью" in h[2]) for h in hits(r, "handler.no-data-exchange-check")] == [(5, True)]
    assert [(h[1], "ПередЗаписью" in h[2]) for h in hits(r, "tx.in-write-handler")] == [(6, True)]
    # вендорский код рядом (ОбработкаПроведения базы) задаче не вменяется
    assert all(f["строка"] >= 4 for f in r["находки"])


def test_internal_api_of_foreign_module(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, "CommonModules/ВендорскийМодуль.xml", ["<MetaDataObject/>"])
    write(repo, "CommonModules/ВендорскийМодуль/Ext/Module.bsl", [
        "#Область ПрограммныйИнтерфейс", "Функция Публичный() Экспорт", "\tВозврат 1;", "КонецФункции",
        "#КонецОбласти",
        "#Область СлужебныйПрограммныйИнтерфейс", "Функция Служебный() Экспорт", "\tВозврат 2;",
        "КонецФункции", "#КонецОбласти"])
    write(repo, f"CommonModules/{PREFIX}Прошлый.xml", ["<MetaDataObject/>"])
    write(repo, f"CommonModules/{PREFIX}Прошлый/Ext/Module.bsl", [
        "#Область СлужебныйПрограммныйИнтерфейс", "Функция ПрошлаяСлужебная() Экспорт", "\tВозврат 4;",
        "КонецФункции", "#КонецОбласти"])
    commit(repo, "вендор и модуль прошлой задачи")
    # модуль, созданный задачей, — «свой» и без префикса
    write(repo, "CommonModules/СвойНовый.xml", ["<MetaDataObject/>"])
    write(repo, "CommonModules/СвойНовый/Ext/Module.bsl", [
        "#Область СлужебныйПрограммныйИнтерфейс", "Функция СвояСлужебная() Экспорт", "\tВозврат 3;",
        "КонецФункции", "#КонецОбласти"])
    write(repo, f"CommonModules/{PREFIX}Новый.xml", ["<MetaDataObject/>"])
    write(repo, f"CommonModules/{PREFIX}Новый/Ext/Module.bsl", [
        "Функция Посчитать() Экспорт",
        "\tА = ВендорскийМодуль.Служебный();",
        "\tБ = ВендорскийМодуль.Публичный();",
        "\tВ = СвойНовый.СвояСлужебная();",
        f"\tГ = {PREFIX}Прошлый.ПрошлаяСлужебная();",
        "\t// Д = ВендорскийМодуль.Служебный(); — в комментарии не вызов",
        "\tВозврат А + Б + В + Г;",
        "КонецФункции"])
    r = check(repo)
    found = hits(r, "call.foreign-internal-api")
    assert [(h[0], h[1]) for h in found] == [(f"CommonModules/{PREFIX}Новый/Ext/Module.bsl", 2)]
    assert "ВендорскийМодуль.Служебный" in found[0][2]


def test_internal_api_without_prefix_setting_still_respects_task_modules(tmp_path):
    """Без префикса в настройках «свои» — только модули, созданные задачей."""
    repo = new_repo(tmp_path)
    write(repo, "CommonModules/Чужой/Ext/Module.bsl", [
        "#Область СлужебныйПрограммныйИнтерфейс", "Функция С() Экспорт", "КонецФункции", "#КонецОбласти"])
    commit(repo)
    write(repo, "CommonModules/Новый.xml", ["<MetaDataObject/>"])
    write(repo, "CommonModules/Новый/Ext/Module.bsl", ["Процедура П()", "\tЧужой.С();", "КонецПроцедуры"])
    r = check(repo, registry(settings={}))
    assert [h[1] for h in hits(r, "call.foreign-internal-api")] == [2]


def test_registry_exception_applies_only_at_its_place(tmp_path):
    repo = new_repo(tmp_path)
    for name in (f"{PREFIX}Разрешённый", f"{PREFIX}Другой"):
        write(repo, f"InformationRegisters/{name}/Ext/ManagerModule.bsl", [
            "Процедура ЗарегистрироватьИзменения(Договоры) Экспорт",
            "\tДля Каждого Договор Из Договоры Цикл",
            "\t\tЗарегистрироватьИзменение(Договор);",
            "\tКонецЦикла;",
            "КонецПроцедуры",
            "",
            "Процедура ЗарегистрироватьИзменение(Договор)",
            "\tЗапись = СоздатьМенеджерЗаписи();",
            "\tЗапись.Договор = Договор;",
            "\tЗапись.Записать();",
            "КонецПроцедуры"])
    allowed = f"InformationRegisters/{PREFIX}Разрешённый/Ext/ManagerModule.bsl"
    reg = registry(exceptions=[{"проверка": "loop.db-write-via-call", "файл": allowed,
                                "содержит": "ЗарегистрироватьИзменение", "основание": "решение по задаче"}])
    r = check(repo, reg)
    assert r["исключены"] == [{"ид": "loop.db-write-via-call", "файл": allowed, "строка": 3,
                               "основание": "решение по задаче"}]
    assert [h[0] for h in hits(r, "loop.db-write-via-call")] == [
        f"InformationRegisters/{PREFIX}Другой/Ext/ManagerModule.bsl"]


def test_out_parameter_without_initialisation(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, f"CommonModules/{PREFIX}Параметры/Ext/Module.bsl", [
        "Процедура А(Параметры)",
        "\tЕсли Параметры.Свойство(\"Ключ\", Значение) Тогда",
        "\tКонецЕсли;",
        "\tБ = 1;",
        "\tПараметры.Свойство(\"К\", Б);",
        "\tПараметры.Свойство(\"К\", Истина);",
        "\tПерем В;",
        "\tПараметры.Свойство(\"К\", в);",
        "КонецПроцедуры"])
    r = check(repo)
    found = hits(r, "out-param.uninitialized")
    assert [h[1] for h in found] == [2] and "«Значение»" in found[0][2]


def test_out_parameter_checks_only_task_lines(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, "CommonModules/Вендор/Ext/Module.bsl",
          ["Процедура А(П)", "\tП.Свойство(\"К\", Старое);", "КонецПроцедуры"])
    commit(repo)
    write(repo, "CommonModules/Вендор/Ext/Module.bsl",
          ["Процедура А(П)", "\tП.Свойство(\"К\", Старое);", "\tП.Свойство(\"К\", Новое);", "КонецПроцедуры"])
    assert [h[1] for h in hits(check(repo), "out-param.uninitialized")] == [3]


def test_bare_blank_line_inside_method(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, f"CommonModules/{PREFIX}Пустые/Ext/Module.bsl", [
        "Процедура А()", "\tЗначение = 1;", "", "\t", "\tЗначение = 2;", "КонецПроцедуры", "",
        "Процедура Б()", "КонецПроцедуры"])
    assert [h[1] for h in hits(check(repo), "layout.bare-blank-line")] == [3]


def test_revision_mode(tmp_path):
    repo = new_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD").strip()
    write(repo, f"CommonModules/{PREFIX}Новый/Ext/Module.bsl",
          ["Процедура П()", "\tДля Каждого Х Из Т Цикл", "\t\tХ.Записать();", "\tКонецЦикла;", "КонецПроцедуры"])
    commit(repo, "задача")
    r = check(repo, base=head, rev="HEAD")
    assert [h[1] for h in hits(r, "loop.db-write")] == [3]


def test_directory_pair_mode(tmp_path):
    before, after = tmp_path / "эталон", tmp_path / "копия"
    for root in (before, after):
        write(root, "Обработка/Ext/ObjectModule.bsl", ["Процедура А()", "\tЗначение = 1;", "КонецПроцедуры"])
    write(after, "Обработка/Ext/ObjectModule.bsl",
          ["Процедура А()", "\tЗначение = 1;", "\tДля Каждого Х Из Т Цикл", "\t\tХ.Записать();",
           "\tКонецЦикла;", "КонецПроцедуры"])
    write(after, "Обработка/Forms/Форма/Ext/Form/Module.bsl",
          ["&НаСервере", "Процедура Б()", "\tТ = ОбщегоНазначения.ЗначениеРеквизитаОбъекта(С, \"Р\");",
           "\tТ = ОбщегоНазначения.ЗначениеРеквизитаОбъекта(С, \"Р2\");", "КонецПроцедуры"])
    r = run_rules_check(DirPairChangeSource(before, after), registry())
    assert [(h[0], h[1]) for h in hits(r, "loop.db-write")] == [("Обработка/Ext/ObjectModule.bsl", 4)]
    assert [h[1] for h in hits(r, "read.repeated-attribute-value")] == [4]


def test_only_and_paths_filters(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, f"CommonModules/{PREFIX}Плохой/Ext/Module.bsl", STRUCT_BAD)
    write(repo, f"CommonModules/{PREFIX}Второй/Ext/Module.bsl",
          ["Процедура П()", "\tДля Каждого Х Из Т Цикл", "\t\tХ.Записать();", "\tКонецЦикла;", "КонецПроцедуры"])
    only = check(repo, only=["loop.db-write", "нет.такого"])
    assert {f["ид"] for f in only["находки"]} == {"loop.db-write"}
    assert {"что": "правило нет.такого", "почему": "нет среди правил инструмента"} in only["пропущено"]
    scoped = check(repo, paths=[f"CommonModules/{PREFIX}Второй"])
    assert {f["файл"] for f in scoped["находки"]} == {f"CommonModules/{PREFIX}Второй/Ext/Module.bsl"}


def test_task_methods(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, "CommonModules/Общий/Ext/Module.bsl",
          ["Процедура Старый()", "\tА = 1;", "КонецПроцедуры", "", "Функция Нетронутая() Экспорт",
           "\tВозврат 1;", "КонецФункции"])
    commit(repo)
    write(repo, "CommonModules/Общий/Ext/Module.bsl",
          ["Процедура Старый()", "\tА = 2;", "КонецПроцедуры", "", "Функция Нетронутая() Экспорт",
           "\tВозврат 1;", "КонецФункции", "", "Функция Новая(П) Экспорт", "\tВозврат П;", "КонецФункции"])
    rows = run_task_methods(GitChangeSource(repo))
    assert [(r["метод"], r["статус"], r["вид"], r["экспорт"], r["модуль"]) for r in rows] == [
        ("Старый", "изменён", "Процедура", False, "ОбщийМодуль.Общий"),
        ("Новая", "новый", "Функция", True, "ОбщийМодуль.Общий"),
    ]


def test_source_must_be_exactly_one():
    for args in ((None, None, None), ("r", "b", "t"), (None, "b", None), (None, None, "t")):
        try:
            source_kind(*args)
            raise AssertionError(f"ожидали ошибку для {args}")
        except InputError:
            pass
    assert source_kind("r", None, None) == "git"
    assert source_kind(None, "b", "t") == "каталоги"
