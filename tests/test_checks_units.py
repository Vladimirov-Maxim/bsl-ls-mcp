"""Чистое ядро проверок кода задачи: разбор строки и модуля, маска, области шаблонов,
разбор реестра (терпимый, но не молчаливый), исключения, ответ договора."""
from __future__ import annotations

import checks_support  # noqa: F401 — путь к src

from bsl_ls_mcp.domain.checks.bsl_line import split_line, split_text, top_level_conjunctions
from bsl_ls_mcp.domain.checks.bsl_module import ModuleModel
from bsl_ls_mcp.domain.checks.engine import FileUnderCheck, check_files
from bsl_ls_mcp.domain.checks.findings import Finding, RegistryException, Skipped, finish
from bsl_ls_mcp.domain.checks.mask import mask_from_texts, mask_from_unified_diff
from bsl_ls_mcp.domain.checks.module_names import module_label
from bsl_ls_mcp.domain.checks.registry import (default_ruleset, example_mismatches,
                                               ruleset_from_registry)


def tpl(rid: str, area: str, pattern: str | None = None, **extra) -> dict:
    rule = {"ид": rid, "вид": "шаблон", "уровень": "Fail", "текст": f"нарушение {rid}",
            "инструмент": "bsl-ls", "где": area, "ловит": ["x"], "не_ловит": ["y"]}
    if pattern is not None:
        rule["шаблон"] = pattern
    rule.update(extra)
    return rule


def run_templates(rules: list[dict], lines: list[str], path: str = "CommonModules/М/Ext/Module.bsl",
                  settings: dict | None = None) -> list[tuple[str, int]]:
    rs = ruleset_from_registry({"правила": rules, "настройки": settings or {}})
    assert not rs.problems, rs.problems
    f = FileUnderCheck(path, "A", lines, frozenset(range(1, len(lines) + 1)))
    return sorted((x.rule_id, x.line) for x in check_files([f], rs))


# --- строка и модуль -------------------------------------------------------------

def test_split_line_parts():
    p = split_line('\tТекст = "ВЫБРАТЬ ""*"" ИЗ"; // НСтр(')
    assert p.code == '\tТекст = ""; ' and p.literal == 'ВЫБРАТЬ "*" ИЗ' and not p.comment
    assert split_line("  // всё комментарий").comment
    cont = split_line('\t|ГДЕ Т.Код = 1";')
    assert cont.continuation and cont.literal == "ГДЕ Т.Код = 1" and cont.code == '"";'


def test_top_level_conjunctions():
    assert top_level_conjunctions("Если А = 1 И Б = 2 Тогда") == 1
    assert top_level_conjunctions("\t\tИли (В = 3 И Г = 4) Тогда") == 0
    assert top_level_conjunctions("\t\tИ Б = 2") == 0


def test_split_text_like_readalllines():
    assert split_text("a\r\nb\r\n") == ["a", "b"]
    assert split_text("a\nb") == ["a", "b"]


def test_module_methods_params_export_regions():
    m = ModuleModel([
        "#Область СлужебныйПрограммныйИнтерфейс",
        "&НаСервере",
        "Функция Ф(А, Б = Новый Структура(), Знач В) Экспорт // комментарий",
        "\tВозврат 1;",
        "КонецФункции",
        "#КонецОбласти",
        "Асинх Процедура П(",
        "\tА,",
        "\tБ)",
        "КонецПроцедуры",
    ])
    f, p = m.methods
    assert (f.name, f.kind, f.export, f.start, f.end) == ("Ф", "Функция", True, 3, 5)
    assert (p.name, p.is_async, p.export, p.decl_end, p.end) == ("П", True, False, 9, 10)
    assert [x.text for x in p.params] == ["А", "Б"]
    assert m.method_regions["ф"] == ("СлужебныйПрограммныйИнтерфейс",)
    assert m.method_regions["п"] == ()


def test_loop_depth_and_try_blocks():
    m = ModuleModel([
        "Процедура П()",
        "\tДля Каждого Х Из Т Цикл",
        "\t\tПопытка",
        "\t\t\tА = 1;",
        "\t\tИсключение",
        "\t\tКонецПопытки;",
        "\tКонецЦикла;",
        "\tБ = 1;",
        "КонецПроцедуры",
    ])
    body = m.body(m.methods[0])
    assert [body.loop[n] for n in range(2, 9)] == [0, 1, 1, 1, 1, 1, 0]
    assert [(t.try_line, t.except_line, t.end_line) for t in body.tries] == [(3, 5, 6)]


# --- маска -----------------------------------------------------------------------

def test_mask_from_unified_diff():
    diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -2,0 +3,2 @@\n+a\n+b\n@@ -10 +12 @@\n-old\n+new\n"
    assert mask_from_unified_diff(diff) == frozenset({3, 4, 12})


def test_mask_from_texts():
    assert mask_from_texts(["a", "b", "c"], ["a", "X", "c", "d"]) == frozenset({2, 4})


# --- шаблоны: области -------------------------------------------------------------

def test_template_areas():
    rules = [
        tpl("код", "Code", r"Сообщить\("),
        tpl("литерал", "Query", r"ВЫБРАТЬ \*"),
        tpl("продолжение", "QueryCont", r"\b(выбрать|где)\b"),
        tpl("комментарий", "Comment", r"TODO"),
        tpl("строка", "Line", r"[«»]"),
        tpl("сырая", "Raw", r"Пароль"),
        tpl("условие", "Cond"),
        tpl("хвост", "Trailing"),
    ]
    lines = [
        "Процедура П()",                                   # 1
        "\tСообщить(\"x\");",                                # 2 код
        "\tТ = \"Сообщить(\"; // Сообщить(",                 # 3 — в литерале и комментарии: не код
        "\tЗ = \"ВЫБРАТЬ * ИЗ Т\";",                         # 4 литерал
        "\t|где Т.А = 1\";",                                 # 5 продолжение
        "\t// TODO «потом»",                                 # 6 комментарий + строка
        "\tЖурнал(\"Пароль\");",                             # 7 сырая
        "\tЕсли А = 1 И Б = 2 Тогда",                        # 8 условие
        "\tКонецЕсли;   ",                                   # 9 хвост
        "КонецПроцедуры",
    ]
    assert run_templates(rules, lines) == sorted([
        ("код", 2), ("литерал", 4), ("продолжение", 5), ("комментарий", 6), ("строка", 6),
        ("сырая", 7), ("условие", 8), ("хвост", 9)])


def test_template_case_sensitive_by_default_and_files_filter():
    rules = [tpl("регистр", "Code", r"\bНЕ\b"), tpl("форма", "Code", r"ЭтаФорма", файлы=r"/Forms/")]
    lines = ["Если Не А Тогда", "Если НЕ Б Тогда", "Т = ЭтаФорма;"]
    assert run_templates(rules, lines) == [("регистр", 2)]
    assert run_templates(rules, lines, path="Catalogs/К/Forms/Ф/Ext/Form/Module.bsl") == [
        ("регистр", 2), ("форма", 3)]


def test_template_global_names_placeholder_and_override():
    rule = tpl("имя", "Code", r"^\s*({ИменаГлобальногоКонтекста})\s*=")
    assert run_templates([rule], ["Строка = 1;", "Шапка = 2;"]) == [("имя", 1)]
    assert run_templates([rule], ["Строка = 1;", "Шапка = 2;"],
                         settings={"имена_глобального_контекста": ["Шапка"]}) == [("имя", 2)]


def test_dotnet_named_groups_are_translated():
    assert run_templates([tpl("группа", "Code", r"(?<имя>Сообщить)\(")], ["Сообщить(1);"]) == [("группа", 1)]


def test_examples_check():
    rs = ruleset_from_registry({"правила": [
        tpl("ок", "Code", r"Сообщить\(", ловит=["\tСообщить(1);"], не_ловит=["\tПоказать(1);"]),
        tpl("плохо", "Code", r"Сообщить\(", ловит=["\tПоказать(1);"], не_ловит=["\tСообщить(1);"]),
    ]})
    by_id = {t.rule_id: example_mismatches(t) for t in rs.templates}
    assert by_id["ок"] == [] and len(by_id["плохо"]) == 2


# --- реестр: терпимо, но не молча ------------------------------------------------------

def test_registry_problems_are_named_not_swallowed():
    rs = ruleset_from_registry({"настройки": {"префикс": 5}, "правила": [
        {"ид": "а", "вид": "код", "уровень": "Fail", "инструмент": "bsl-ls"},
        {"ид": "б", "вид": "код", "уровень": "Fail", "инструмент": "bsl-ls", "реализация": "нет.такой"},
        tpl("в", "Новая"),
        tpl("г", "Code", "(незакрыта"),
        tpl("д", "Code"),
        {"ид": "е", "вид": "код", "уровень": "Критично", "инструмент": "bsl-ls", "реализация": "loop.db-read"},
        {"ид": "ж", "вид": "код", "уровень": "Warn", "инструмент": "1c-meta"},
        {"ид": "з", "вид": "код", "уровень": "Warn", "инструмент": "bsl-ls", "реализация": "loop.db-read",
         "незнакомое_поле": True},
    ]})
    problems = {p.what: p.why for p in rs.problems}
    assert "реализация" in problems["правило а"]
    assert "нет.такой" in problems["правило б"]
    assert "Новая" in problems["правило в"]
    assert "не компилируется" in problems["правило г"]
    assert "нет шаблона" in problems["правило д"]
    assert "Критично" in problems["правило е"]
    assert "настройки реестра" in problems
    assert "правило ж" not in problems                      # чужое правило — не наше дело
    assert [r.rule_id for r in rs.code_rules] == ["з"]       # незнакомое поле не мешает


def test_registry_without_our_rules_is_reported_not_clean():
    rs = ruleset_from_registry({"правила": [{"ид": "1", "вид": "код", "уровень": "Fail"}]})
    assert not rs.code_rules and any(p.what == "реестр" for p in rs.problems)
    assert ruleset_from_registry([]).problems[0].what == "реестр"


def test_default_ruleset_without_registry():
    rs = default_ruleset()
    assert rs.templates == () and len(rs.code_rules) == 24
    assert all(r.rule_id == r.check.key for r in rs.code_rules)


def test_project_renumbering_does_not_touch_the_tool():
    """Номер, уровень и текст — данные проекта: перенумеровали — инструмент тот же."""
    reg = {"правила": [{"ид": "99.1", "вид": "код", "уровень": "Warn", "текст": "своё",
                        "инструмент": "bsl-ls", "реализация": "loop.db-write"}]}
    lines = ["Процедура П()", "\tДля Каждого Х Из Т Цикл", "\t\tХ.Записать();", "\tКонецЦикла;",
             "КонецПроцедуры"]
    f = FileUnderCheck("CommonModules/М/Ext/Module.bsl", "A", lines, frozenset(range(1, 6)))
    [x] = check_files([f], ruleset_from_registry(reg))
    assert (x.rule_id, x.level, x.text, x.line) == ("99.1", "Warn", "своё", 3)


def test_params_threshold_from_registry():
    reg = {"правила": [{"ид": "п", "вид": "код", "уровень": "Warn", "инструмент": "bsl-ls",
                        "реализация": "params.too-many", "параметры": {"порог": 2}}]}
    f = FileUnderCheck("M.bsl", "A", ["Процедура П(А, Б, В)", "КонецПроцедуры"], frozenset({1, 2}))
    [x] = check_files([f], ruleset_from_registry(reg))
    assert x.line == 1 and "3 при пороге 2" in x.text


# --- ответ договора ---------------------------------------------------------------------

def test_finish_orders_dedupes_and_excludes():
    a = Finding("2.1", "Warn", "Б.bsl", 5, "в цикле вызывается ЗаписатьВсё", None)
    b = Finding("1.1", "Fail", "А.bsl", 9, "т", "3.1")
    out = finish([a, b, b], [RegistryException("2.1", "Б.bsl", "ЗаписатьВсё", "так решено")],
                 [Skipped("правило 9", "почему"), Skipped("правило 9", "почему")])
    assert out["находки"] == [{"ид": "1.1", "уровень": "Fail", "файл": "А.bsl", "строка": 9,
                               "текст": "т", "регламент": "3.1"}]
    assert out["исключены"] == [{"ид": "2.1", "файл": "Б.bsl", "строка": 5, "основание": "так решено"}]
    assert out["пропущено"] == [{"что": "правило 9", "почему": "почему"}]


def test_module_label():
    assert module_label("CommonModules/Х/Ext/Module.bsl") == "ОбщийМодуль.Х"
    assert module_label("Documents/Д/Ext/ObjectModule.bsl") == "Документ.Д.МодульОбъекта"
    assert module_label("Catalogs/К/Forms/Ф/Ext/Form/Module.bsl") == "Справочник.К.Форма.Ф"
    assert module_label("Обработка/Ext/ObjectModule.bsl") == "Обработка/Ext/ObjectModule.bsl"
