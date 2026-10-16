"""Источник диагностик: тёплый LSP (контекст конфигурации) для кода корпуса, пока индекс
строится — analyze с прогревом сервера в фоне; код вне корпуса и снипет — analyze."""
import asyncio
import dataclasses

from test_tools_mock import WS, FakeAnalyzer, FakeLsp, _settings

from bsl_ls_mcp.application import tools as T

MOD = WS / "CommonModules" / "ТестОбщийМодуль" / "Ext" / "Module.bsl"
LSP_DIAG = [{"range": {"start": {"line": 2}}, "code": "MissingCommonModuleMethod", "severity": 1,
             "message": "Метод X общего модуля Y не существует"},
            {"range": {"start": {"line": 5}}, "code": "LineLength", "severity": 3, "message": "длинно"}]
ANALYZE_RAW = [{"range": {"start": {"line": 0}}, "code": "UnknownMember", "severity": "Error",
                "message": "ложное", "_src_uri": MOD.resolve().as_uri()}]


def _run(lsp, analyzer, engine="auto", **kw):
    s = dataclasses.replace(_settings(), diag_engine=engine)
    return asyncio.run(T.bsl_diagnostics(T.Deps(lsp=lsp, analyzer=analyzer, settings=s), **kw))


def test_ready_index_gives_lsp_diagnostics_with_config_context():
    lsp, fa = FakeLsp(ready=True, diags={"Module.bsl": LSP_DIAG}), FakeAnalyzer(ANALYZE_RAW)
    r = _run(lsp, fa, module_full_name="ОбщийМодуль.ТестОбщийМодуль")
    assert r["источник"] == {"движок": "lsp"}
    assert fa.seen_dir is None                                     # analyze не звали
    assert [(d["code"], d["severity"], d["line"], d["file"]) for d in r["diagnostics"]] == [
        ("MissingCommonModuleMethod", "error", 3, "ОбщийМодуль.ТестОбщийМодуль")]
    assert r["suppressed"]["by_code"] == [{"code": "LineLength", "severity": "info", "count": 1}]
    assert lsp.diag_uris == [MOD.resolve().as_uri()]


def test_index_building_falls_back_to_analyze_and_warms_up():
    lsp, fa = FakeLsp(ready=False), FakeAnalyzer(ANALYZE_RAW)
    r = _run(lsp, fa, module_full_name="ОбщийМодуль.ТестОбщийМодуль")
    assert r["источник"]["движок"] == "analyze" and "индекс строится" in r["источник"]["примечание"]
    assert lsp.warmed == 1 and lsp.diag_uris == []
    assert [d["code"] for d in r["diagnostics"]] == ["UnknownMember"]


def test_engine_analyze_keeps_previous_behaviour():
    lsp, fa = FakeLsp(ready=True), FakeAnalyzer([])
    r = _run(lsp, fa, engine="analyze", module_full_name="ОбщийМодуль.ТестОбщийМодуль")
    assert r["источник"] == {"движок": "analyze"} and lsp.warmed == 0 and lsp.diag_uris == []


def test_lsp_timeout_is_not_reported_as_clean():
    class Slow(FakeLsp):
        async def diagnostics(self, uri):
            raise asyncio.TimeoutError
    fa = FakeAnalyzer(ANALYZE_RAW)
    r = _run(Slow(ready=True), fa, module_full_name="ОбщийМодуль.ТестОбщийМодуль")
    assert r["источник"]["движок"] == "analyze" and "LSP не ответил" in r["источник"]["примечание"]
    assert [d["code"] for d in r["diagnostics"]] == ["UnknownMember"]


def test_path_inside_workspace_uses_lsp_for_that_file_only():
    lsp = FakeLsp(ready=True, diags={"Module.bsl": LSP_DIAG})
    r = _run(lsp, FakeAnalyzer([]), path=str(MOD))
    assert r["источник"] == {"движок": "lsp"} and lsp.diag_uris == [MOD.resolve().as_uri()]


def test_path_outside_workspace_and_snippet_stay_on_analyze(tmp_path):
    (tmp_path / "Внешняя.bsl").write_text("Процедура П()\nКонецПроцедуры\n", encoding="utf-8")
    lsp = FakeLsp(ready=True)
    assert _run(lsp, FakeAnalyzer([]), path=str(tmp_path))["источник"] == {"движок": "analyze"}
    assert _run(lsp, FakeAnalyzer([]), text="А = 1;")["источник"] == {"движок": "analyze"}
    assert lsp.diag_uris == [] and lsp.warmed == 0


def test_too_many_files_go_to_analyze():
    s_lsp = FakeLsp(ready=True)
    fa = FakeAnalyzer([])
    s = dataclasses.replace(_settings(), diag_lsp_max_files=0)
    r = asyncio.run(T.bsl_diagnostics(T.Deps(lsp=s_lsp, analyzer=fa, settings=s),
                                      module_full_name="ОбщийМодуль.ТестОбщийМодуль"))
    assert r["источник"]["движок"] == "analyze" and "файлов больше" in r["источник"]["примечание"]
    assert s_lsp.diag_uris == []
