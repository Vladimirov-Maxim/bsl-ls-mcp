"""Зона правок в bsl_diagnostics (ЗИ-11, BSL-1 п. 1): lines / function — только диагностики
зоны, все уровни, без сворачивания; оба источника."""
import asyncio
import dataclasses

import pytest
from test_tools_mock import WS, FakeAnalyzer, FakeLsp, _settings

from bsl_ls_mcp.application import tools as T
from bsl_ls_mcp.domain.line_filter import in_ranges, method_ranges, parse_lines

MOD = WS / "CommonModules" / "ТестОбщийМодуль" / "Ext" / "Module.bsl"
URI = MOD.resolve().as_uri()


def _d(line, code, sev="Information"):
    return {"range": {"start": {"line": line - 1}}, "code": code, "severity": sev, "message": code, "_src_uri": URI}


RAW = [_d(2, "MissingSpace"), _d(4, "LineLength"), _d(6, "Ошибка", "Error"), _d(11, "DeprecatedMessage", "Warning")]


def _run(lsp=None, raw=RAW, **kw):
    s = dataclasses.replace(_settings(), diag_engine="analyze")
    return asyncio.run(T.bsl_diagnostics(T.Deps(lsp=lsp or FakeLsp(), analyzer=FakeAnalyzer(raw), settings=s), **kw))


def test_parse_lines_and_ranges():
    assert parse_lines("2660-2781") == ((2660, 2781),)
    assert parse_lines(" 10-20, 35 ;40-50") == ((10, 20), (35, 35), (40, 50))
    for bad in ("", "a-b", "20-10", "0-3"):
        with pytest.raises(ValueError):
            parse_lines(bad)
    assert in_ranges(35, ((10, 20), (35, 35))) and not in_ranges(21, ((10, 20),))


def test_method_ranges_include_directives():
    lines = ["&НаКлиенте", "Процедура А()", "КонецПроцедуры", "", "// описание", "&НаСервере",
             "&Перед(\"Б\")", "Функция Б()", "\tВозврат 1;", "КонецФункции"]
    assert method_ranges(lines, ["а", "Б", "Нет"]) == {"А": (1, 3), "Б": (6, 10)}


def test_lines_zone_shows_all_levels_of_zone_only():
    r = _run(module_full_name="ОбщийМодуль.ТестОбщийМодуль", lines="1-8")
    assert [(d["line"], d["code"]) for d in r["diagnostics"]] == [(6, "Ошибка"), (2, "MissingSpace"), (4, "LineLength")]
    assert r["suppressed"]["total"] == 0                      # все уровни — без сворачивания
    assert r["источник"]["фильтр"] == {"строки": [[1, 8]]}


def test_function_zone_by_name():
    r = _run(module_full_name="ОбщийМодуль.ТестОбщийМодуль", function="записатьрезультат")
    assert [(d["line"], d["code"]) for d in r["diagnostics"]] == [(11, "DeprecatedMessage")]
    assert r["источник"]["фильтр"] == {"методы": {"ЗаписатьРезультат": [10, 12]}}


def test_zone_with_lsp_source():
    lsp_diags = [{"range": {"start": {"line": 5}}, "code": "Ошибка", "severity": 1, "message": "e"},
                 {"range": {"start": {"line": 10}}, "code": "Вне", "severity": 1, "message": "e"}]
    lsp = FakeLsp(ready=True, diags={"Module.bsl": lsp_diags})
    s = dataclasses.replace(_settings(), diag_engine="auto")
    r = asyncio.run(T.bsl_diagnostics(T.Deps(lsp=lsp, analyzer=FakeAnalyzer([]), settings=s),
                                      module_full_name="ОбщийМодуль.ТестОбщийМодуль", function="ВычислитьСумму"))
    assert r["источник"]["движок"] == "lsp" and [d["code"] for d in r["diagnostics"]] == ["Ошибка"]


def test_zone_ignores_size_folding():
    many = [_d(3, f"Код{i}", "Warning") for i in range(300)]
    s = dataclasses.replace(_settings(), diag_engine="analyze", diag_max_chars=500)
    r = asyncio.run(T.bsl_diagnostics(T.Deps(lsp=FakeLsp(), analyzer=FakeAnalyzer(many), settings=s),
                                      module_full_name="ОбщийМодуль.ТестОбщийМодуль", lines="3"))
    assert len(r["diagnostics"]) == 300 and r["suppressed"]["total"] == 0


def test_zone_errors_are_explicit():
    with pytest.raises(T.ResolveError, match="lines ИЛИ function"):
        _run(module_full_name="ОбщийМодуль.ТестОбщийМодуль", lines="1-2", function="ВычислитьСумму")
    with pytest.raises(T.ResolveError, match="метод не найден"):
        _run(module_full_name="ОбщийМодуль.ТестОбщийМодуль", function="НетТакого")
    with pytest.raises(T.ResolveError, match="lines"):
        _run(module_full_name="ОбщийМодуль.ТестОбщийМодуль", lines="десять")


def test_lines_needs_single_file(tmp_path):
    for name in ("ManagerModule.bsl", "ObjectModule.bsl"):
        (tmp_path / name).write_text("Процедура П()\nКонецПроцедуры\n", encoding="utf-8")
    with pytest.raises(T.ResolveError, match="одному файлу"):
        _run(path=str(tmp_path), lines="1-2")
    r = _run(path=str(tmp_path), function="П", raw=[])        # function ищется по всем файлам
    assert set(r["источник"]["фильтр"]["методы"]) == {"ManagerModule.bsl:П", "ObjectModule.bsl:П"}


def test_snippet_zone():
    r = _run(text="А = 1;\nБ = 2;\nВ = 3;\n", lines="2", raw=[])
    assert r["источник"]["фильтр"] == {"строки": [[2, 2]]}
