"""Проверки модулей форм: присваивание свойству формы (ЗИ-45) и серверный метод в клиентском
событии с платформенным запретом (ЗИ-35). Описание формы — EDT Form.form и конфигуратор Form.xml."""
from checks_support import commit, hits, new_repo, registry, write

from bsl_ls_mcp.application.rules_check import run_rules_check
from bsl_ls_mcp.domain.checks.form_events import form_files_for, handlers_from_code, handlers_from_form
from bsl_ls_mcp.infrastructure.git_changes import GitChangeSource

FORM_D = "Catalogs/Спр/Forms/ФормаЭлемента/Ext/Form/Module.bsl"
FORM_E = "BF/src/Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl"


def _check(repo, rule):
    return hits(run_rules_check(GitChangeSource(repo, "HEAD"), registry()), rule)


def test_form_files_and_handler_parsing():
    assert form_files_for(FORM_D) == ["Catalogs/Спр/Forms/ФормаЭлемента/Ext/Form.xml"]
    assert form_files_for(FORM_E) == ["BF/src/Catalogs/Спр/Forms/ФормаЭлемента/Form.form"]
    edt = "<handlers>\n  <event>StartChoice</event>\n  <name>ПолеНачалоВыбора</name>\n</handlers>"
    des = '<Events><Event name="StartChoice">ПолеНачалоВыбора</Event></Events>'
    assert handlers_from_form(edt) == handlers_from_form(des) == {"поленачаловыбора": {"StartChoice"}}
    assert handlers_from_code(['\tЭлементы.Поле.УстановитьДействие("НачалоВыбора", "Подключаемый_НВ");',
                               '// Элементы.Поле.УстановитьДействие("НачалоВыбора", "Комментарий");']) == {
        "подключаемый_нв": {"НачалоВыбора"}}


def test_assign_form_property(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, FORM_D, ["&НаКлиенте", "Процедура Старый()", "\tЗаголовок = \"старый код\";", "КонецПроцедуры"])
    write(repo, "CommonModules/Общ/Ext/Module.bsl", ["Процедура П()", "КонецПроцедуры"])
    commit(repo)
    write(repo, FORM_D, ["&НаКлиенте", "Процедура Старый()", "\tЗаголовок = \"старый код\";", "КонецПроцедуры", "",
                         "&НаКлиенте", "Процедура Разбить(Ссылки)",
                         "\tДоступность = ПроверитьДоступность(Ссылки);",          # 8 — находка (см. 9)
                         "\tЕсли Не Доступность.Разрешено Тогда",
                         "\t\tВозврат;",
                         "\tКонецЕсли;",
                         "\tЭтотОбъект.ТолькоПросмотр = Истина;",                   # явно — нет
                         "\tМодифицированность = Ложь;",                             # намеренная запись — нет
                         "\tЗаголовок = Новый Структура;",                           # 14 — находка (объект)
                         "КонецПроцедуры", "",
                         "&НаКлиенте", "Процедура СПеременной(Заголовок)",
                         "\tПерем Доступность;",
                         "\tДоступность = Новый Структура;",                         # объявлена — нет
                         "\tЗаголовок = Новый Массив;",                              # параметр — нет
                         "КонецПроцедуры"])
    write(repo, "CommonModules/Общ/Ext/Module.bsl", ["Процедура П()", "\tДоступность = Новый Структура;",
                                                      "КонецПроцедуры"])
    found = _check(repo, "form.assign-form-property")
    assert [(f, n) for f, n, _ in found] == [(FORM_D, 8), (FORM_D, 14)]
    assert "в строке 9" in found[0][2] and "справа — объект" in found[1][2]


MODULE = ["&НаКлиенте", "Процедура ПолеНачалоВыбора(Элемент, ДанныеВыбора, СтандартнаяОбработка)",
          "\tПомочь();", "\tСерверБезКонтекста();", "КонецПроцедуры", "",
          "&НаКлиенте", "Процедура Помочь()", "\tЗаполнитьНаСервере();", "КонецПроцедуры", "",
          "&НаСервере", "Процедура ЗаполнитьНаСервере()", "КонецПроцедуры", "",
          "&НаСервереБезКонтекста", "Процедура СерверБезКонтекста()", "КонецПроцедуры", "",
          "&НаКлиенте", "Процедура ПолеПриИзменении(Элемент)", "\tЗаполнитьНаСервере();", "КонецПроцедуры", "",
          "&НаКлиенте", "Процедура Подключаемый_НВ(Элемент, ДанныеВыбора, СтандартнаяОбработка)",
          "\tБезДирективы();", "КонецПроцедуры", "",
          "Процедура БезДирективы()", "КонецПроцедуры", "",
          "&НаСервере", "Процедура ПриСозданииНаСервере(Отказ, СтандартнаяОбработка)",
          "\tЭлементы.Поле.УстановитьДействие(\"НачалоВыбора\", \"Подключаемый_НВ\");", "КонецПроцедуры"]


def _form_repo(tmp_path, module_path, form_path, form_text):
    repo = new_repo(tmp_path)
    write(repo, form_path, [form_text], eol="\n")
    commit(repo)
    write(repo, module_path, MODULE)                     # модуль формы — новый файл задачи
    return repo


def test_server_call_in_client_event_designer(tmp_path):
    repo = _form_repo(tmp_path, FORM_D, "Catalogs/Спр/Forms/ФормаЭлемента/Ext/Form.xml",
                      '<Events><Event name="StartChoice">ПолеНачалоВыбора</Event>'
                      '<Event name="OnChange">ПолеПриИзменении</Event></Events>')
    found = _check(repo, "form.server-call-in-client-event")
    assert [(n, t.split(": ", 1)[1]) for _, n, t in found] == [
        (3, "событие StartChoice: ПолеНачалоВыбора → Помочь → ЗаполнитьНаСервере (&НаСервере)"),
        (27, "событие НачалоВыбора: Подключаемый_НВ → БезДирективы (&НаСервере)"),
    ]


def test_server_call_in_client_event_edt(tmp_path):
    repo = _form_repo(tmp_path, FORM_E, "BF/src/Catalogs/Спр/Forms/ФормаЭлемента/Form.form",
                      "<handlers><event>StartChoice</event><name>ПолеНачалоВыбора</name></handlers>")
    write(repo, "BF/src/Configuration/Configuration.mdo", ["<mdclass:Configuration/>"], eol="\n")
    found = _check(repo, "form.server-call-in-client-event")
    assert [n for _, n, _ in found] == [3, 27]


def test_events_list_from_registry_settings(tmp_path):
    repo = _form_repo(tmp_path, FORM_D, "Catalogs/Спр/Forms/ФормаЭлемента/Ext/Form.xml",
                      '<Events><Event name="OnChange">ПолеПриИзменении</Event></Events>')
    reg = registry(settings={"события_без_серверных_вызовов": ["OnChange"]})
    found = hits(run_rules_check(GitChangeSource(repo, "HEAD"), reg), "form.server-call-in-client-event")
    assert [n for _, n, _ in found] == [22]
