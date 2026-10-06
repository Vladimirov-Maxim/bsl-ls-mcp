"""Приёмка переноса на НАСТОЯЩЕМ реестре проекта и копии бенчмарка — только локально.

Реестр и бенчмарк в репозиторий не попадают (реестр — данные проекта, в бенчмарке код
заказчика), поэтому тесты берут их из переменных окружения и без них пропускаются:

    BSL_RULES_REGISTRY   путь к реестру правил проекта (JSON)
    BSL_BENCH_REPO       копия бенчмарка (git-репозиторий выгрузки)
    BSL_BENCH_BASE       база задачи в копии бенчмарка
    BSL_BENCH_REFERENCE  эталон: JSON старого исполнителя проверок на том же входе

Номера и тексты правил проекта здесь не записаны: ожидания строятся по строкам кода
и по самому реестру, чтобы публичный тест не знал правил проекта."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from checks_support import commit, git, new_repo, write

from bsl_ls_mcp.application.rules_check import run_rules_check
from bsl_ls_mcp.domain.checks.registry import TOOL, example_mismatches, ruleset_from_registry
from bsl_ls_mcp.infrastructure.git_changes import GitChangeSource
from bsl_ls_mcp.infrastructure.registry_file import load_registry

REGISTRY = os.environ.get("BSL_RULES_REGISTRY")
needs_registry = pytest.mark.skipif(not REGISTRY, reason="нет BSL_RULES_REGISTRY — реестр проекта внешний")


def _registry() -> dict:
    return load_registry(REGISTRY)


def _template_ids() -> list[str]:
    if not REGISTRY:
        return []
    return [t.rule_id for t in ruleset_from_registry(_registry()).templates]


@needs_registry
def test_registry_is_fully_executable():
    """Все правила инструмента выполнимы: у «код» есть реализация, шаблоны компилируются."""
    rs = ruleset_from_registry(_registry())
    assert not rs.problems, [(p.what, p.why) for p in rs.problems]
    own = [r for r in _registry()["правила"] if r.get("инструмент") == TOOL]
    assert len(rs.templates) + len(rs.code_rules) == len(own)


@needs_registry
@pytest.mark.parametrize("rule_id", _template_ids() or ["—"])
def test_template_examples(rule_id):
    rule = next(t for t in ruleset_from_registry(_registry()).templates if t.rule_id == rule_id)
    assert example_mismatches(rule) == []


def _template_lines(result: dict, path: str, template_ids: set[str]) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for f in result["находки"]:
        if f["файл"] == path and f["ид"] in template_ids:
            out.setdefault(f["строка"], set()).add(f["ид"])
    return out


@needs_registry
def test_forbidden_constructs_in_new_untracked_module(tmp_path):
    reg = _registry()
    templates = {t.rule_id for t in ruleset_from_registry(reg).templates}
    repo = new_repo(tmp_path)
    path = "CommonModules/МодульПримеров/Ext/Module.bsl"
    write(repo, path, [
        "Процедура Обработать(Строка, Отказ) Экспорт",                                     # 1
        "\tВызватьИсключение НСтр(\"ru = 'Ошибка'\");",                                      # 2
        "\tЕсли НЕ Готово Тогда",                                                           # 3
        "\t\tВозврат;",
        "\tКонецЕсли;",
        "\tЕсли А = 1 И Б = 2 Тогда",                                                       # 6
        "\t\tВозврат;",
        "\tКонецЕсли;",
        "\tВопрос(\"Продолжить?\", РежимДиалогаВопрос.ДаНет);",                              # 9
        "\tСообщить(\"отладка\");",                                                          # 10
        "\tстрИмя = \"x\";",                                                                 # 11
        "\tДля Каждого Дата Из Даты Цикл",                                                  # 12
        "\tКонецЦикла;",
        "\tОтказ = Ложь;",                                                                  # 14
        "\tТекст = ПодробноеПредставлениеОшибки(ИнформацияОбОшибке());",                     # 15
        "\tДополнительныеСвойства.Вставить(\"Ключ\", Истина);",                              # 16
        "\tЭлементы.Поле.УстановитьДействие(\"ПриИзменении\", \"ПолеПриИзменении\");",       # 17
        "\tЗапрос = Новый Запрос(\"ВЫБРАТЬ * ИЗ Справочник.Х КАК Х ДЛЯ ИЗМЕНЕНИЯ\");",        # 18
        "\tЗначение = 1;   ",                                                                # 19
        "КонецПроцедуры",
        "",
        "Функция ПолучитьДанные()",                                                         # 22
        "\tВозврат 1;",
        "КонецФункции",
    ])
    lines = _template_lines(run_rules_check(GitChangeSource(repo), reg), path, templates)
    assert set(lines) == {1, 2, 3, 6, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 22}, lines
    assert len(lines[18]) >= 2          # и «ВЫБРАТЬ *», и «ДЛЯ ИЗМЕНЕНИЯ»


@needs_registry
def test_similar_correct_constructs_are_silent(tmp_path):
    reg = _registry()
    templates = {t.rule_id for t in ruleset_from_registry(reg).templates}
    prefix = (reg.get("настройки") or {}).get("префикс", "")
    prefix = prefix[0] if isinstance(prefix, list) else prefix
    repo = new_repo(tmp_path)
    path = "CommonModules/МодульПримеров/Ext/Module.bsl"
    write(repo, path, [
        "// НСтр( и НЕ в комментарии не код; Сообщить( тоже",
        "Процедура Обработать(СтрокаТаблицы, Отказ) Экспорт",
        "\t",
        "\tЕсли Не Готово Тогда",
        "\t\tОтказ = Истина;",
        "\tКонецЕсли;",
        "\t",
        "\tЕсли А = 1",
        "\t\tИ Б = 2",
        "\t\tИли (В = 3 И Г = 4) Тогда",
        "\t\tВозврат;",
        "\tКонецЕсли;",
        "\t",
        "\tСтрокиНабора = 1;",
        "\tТекст = \"НСтр( и НЕ и Сообщить( внутри строки\";",
        "\tПоказатьПредупреждение(, Текст);",
        "\tДля каждого СтрокаФакта Из Факты Цикл",
        "\tКонецЦикла;",
        "\tРезультат = Запрос.Выполнить();",
        "\tПодробно = ОбработкаОшибок.ПодробноеПредставлениеОшибки(ИнформацияОбОшибке());",
        f"\tДополнительныеСвойства.Вставить(\"{prefix}Ключ\", Истина);",
        "\tЭлементы.Поле.УстановитьДействие(\"ПриИзменении\", \"Подключаемый_ПолеПриИзменении\");",
        "\tЗапрос.Текст =",
        "\t\"ВЫБРАТЬ",
        "\t|\tТ.Ссылка КАК Ссылка",
        "\t|ИЗ",
        "\t|\tСправочник.Х КАК Т",
        "\t|ГДЕ",
        "\t|\tНЕ Т.ПометкаУдаления",
        "\t|\tИ Т.Код = 1 ИЛИ Т.Код = 2\";",
        "\t",
        "КонецПроцедуры",
        "",
        "Функция НовыйНаборПараметров()",
        "\tВозврат Новый Структура;",
        "КонецФункции",
    ])
    assert _template_lines(run_rules_check(GitChangeSource(repo), reg), path, templates) == {}


@needs_registry
def test_templates_in_revision_mode_and_new_files(tmp_path):
    reg = _registry()
    templates = {t.rule_id for t in ruleset_from_registry(reg).templates}
    repo = new_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD").strip()
    path = "CommonModules/Новый/Ext/Module.bsl"
    write(repo, path, ["Процедура А() Экспорт", "\tВызватьИсключение НСтр(\"ru = 'x'\");", "КонецПроцедуры"])
    commit(repo, "задача")
    by_rev = run_rules_check(GitChangeSource(repo, base, "HEAD"), reg)
    assert 2 in _template_lines(by_rev, path, templates)

    repo2 = new_repo(tmp_path, "второй")
    write(repo2, path, ["Процедура А() Экспорт", "\tТекст = «ёлочки»;", "КонецПроцедуры"])
    assert 2 in _template_lines(run_rules_check(GitChangeSource(repo2), reg), path, templates)


BENCH = {k: os.environ.get(k) for k in ("BSL_BENCH_REPO", "BSL_BENCH_BASE", "BSL_BENCH_REFERENCE")}


@pytest.mark.skipif(not (REGISTRY and all(BENCH.values())),
                    reason="нет BSL_RULES_REGISTRY / BSL_BENCH_* — бенчмарк только локально")
def test_benchmark_equivalence():
    """Находки инструмента = находки эталона по (ид, файл, строка) для правил инструмента.
    Находка эталона без строки (было «одна на файл») сравнивается по (ид, файл)."""
    reg = _registry()
    own = {r["ид"] for r in reg["правила"] if r.get("инструмент") == TOOL}
    reference = json.loads(Path(BENCH["BSL_BENCH_REFERENCE"]).read_bytes().decode("utf-8-sig"))
    mine = run_rules_check(GitChangeSource(BENCH["BSL_BENCH_REPO"], BENCH["BSL_BENCH_BASE"]), reg)

    def keys(items: list[dict], by_file: set[str]) -> set[tuple]:
        return {(f["ид"], f["файл"]) if f["ид"] in by_file else (f["ид"], f["файл"], f["строка"])
                for f in items if f["ид"] in own}

    per_file = {f["ид"] for f in reference["находки"] if f["ид"] in own and f["строка"] is None}
    expected, got = keys(reference["находки"], per_file), keys(mine["находки"], per_file)
    assert got == expected, {"только в эталоне": sorted(expected - got), "только в инструменте": sorted(got - expected)}
    assert keys(mine["исключены"], set()) == keys(reference["исключены"], set())
    assert mine["пропущено"] == []
