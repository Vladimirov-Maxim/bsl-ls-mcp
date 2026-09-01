"""Юнит-тесты семи MCP-инструментов через ПОДМЕНУ портов (LspServer/CodeAnalyzer).
Без java/LSP: фейковые порты отдают канонические LSP-ответы, проверяем маппинг
имя→позиция→ответ и бизнес-логику (свёртка severity, фильтр метода, реиндекс).
Запуск: PYTHONPATH=src py -3 -m pytest tests/test_tools_mock.py  (или мини-раннер снизу)."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bsl_ls_mcp.application import tools as T          # noqa: E402
from bsl_ls_mcp.settings import Settings, get_settings  # noqa: E402

WS = ROOT / "tests" / "fixtures" / "smoke_ws"
MOD_URI = (WS / "CommonModules" / "ТестОбщийМодуль" / "Ext" / "Module.bsl").resolve().as_uri()
CALLER_URI = "file:///C:/x/CommonModules/Вызыватель/Ext/Module.bsl"
MGR_URI = "file:///C:/x/Catalogs/Валюты/Ext/ManagerModule.bsl"


def _settings() -> Settings:
    # workspace = фикстура; allowed_roots = она же (иначе path-режим отвергнет конфайнментом)
    return Settings(**{**get_settings().__dict__, "workspace": WS.resolve(),
                       "allowed_roots": (WS.resolve(),)})


class FakeLsp:
    """Порт LspServer: канонические ответы + запись фактов вызова."""

    def __init__(self, **canned):
        self.canned = canned
        self.restarted = False

    async def document_symbol(self, uri):
        # позиция объявления искомого метода в запрошенном модуле
        return [{"name": "ВычислитьСумму", "selectionRange": {"start": {"line": 15, "character": 8}}}]

    async def prepare_call_hierarchy(self, uri, line, character):
        return [{"uri": uri, "name": "ВычислитьСумму", "kind": 12}]

    async def incoming_calls(self, item):
        return [{"from": {"uri": CALLER_URI, "name": "Обработать", "kind": 12}}]

    async def outgoing_calls(self, item):
        return [{"to": {"uri": MGR_URI, "name": "ПриЗаписи", "kind": 6}}]

    async def definition(self, uri, line, character):
        return [{"uri": MOD_URI, "range": {"start": {"line": 0}}}]

    async def references(self, uri, line, character):
        return [{"uri": MOD_URI, "range": {"start": {"line": 0}}},
                {"uri": MOD_URI, "range": {"start": {"line": 0}}}]

    async def code_lens(self, uri):
        return [
            {"data": {"id": "CognitiveComplexity", "methodName": "ВычислитьСумму"}},
            {"data": {"id": "CyclomaticComplexity", "methodName": "ВычислитьСумму"}},
            {"data": {"id": "RunTest", "methodName": "ВычислитьСумму"}},  # не сложность → отброшен
        ]

    async def resolve_code_lens(self, lens):
        kind = mapper_kind(lens)
        num = {"cognitive": 7, "cyclomatic": 3}.get(kind, 0)
        return {"command": {"title": f"Сложность: {num}"}}

    async def restart(self):
        self.restarted = True


def mapper_kind(lens):
    from bsl_ls_mcp.domain import mapper
    return mapper.lens_complexity_kind(lens)


class FakeAnalyzer:
    def __init__(self, raw):
        self.raw = raw
        self.seen_dir = None

    async def analyze(self, src_dir):
        self.seen_dir = src_dir
        return self.raw


def _deps(lsp=None, analyzer=None):
    return T.Deps(lsp=lsp or FakeLsp(), analyzer=analyzer or FakeAnalyzer([]), settings=_settings())


# ---------- навигация ----------
def test_callers_maps_incoming():
    r = asyncio.run(T.bsl_callers(_deps(), "ОбщийМодуль.ТестОбщийМодуль.ВычислитьСумму"))
    assert r == [{"name": "Обработать", "type": "ОбщийМодуль",
                  "full_name": "ОбщийМодуль.Вызыватель.Обработать", "kind": "function"}]


def test_callees_maps_outgoing():
    r = asyncio.run(T.bsl_callees(_deps(), "ОбщийМодуль.ТестОбщийМодуль.ВычислитьСумму"))
    assert r == [{"name": "ПриЗаписи", "type": "Справочник",
                  "full_name": "Справочник.Валюты.ПриЗаписи", "kind": "procedure"}]


def test_callers_empty_when_no_hierarchy_item():
    class NoItem(FakeLsp):
        async def prepare_call_hierarchy(self, uri, line, character):
            return []
    assert asyncio.run(T.bsl_callers(_deps(lsp=NoItem()), "ОбщийМодуль.ТестОбщийМодуль.ВычислитьСумму")) == []


def test_definition_reads_source_line():
    r = asyncio.run(T.bsl_definition(_deps(), "ОбщийМодуль.ТестОбщийМодуль.ВычислитьСумму"))
    assert len(r) == 1
    assert r[0]["type"] == "ОбщийМодуль" and r[0]["module"] == "ТестОбщийМодуль"
    assert r[0]["line"] == 1 and r[0]["text"].startswith("Функция ВычислитьСумму")


def test_references_returns_all_locations():
    r = asyncio.run(T.bsl_references(_deps(), "ОбщийМодуль.ТестОбщийМодуль.ВычислитьСумму"))
    assert len(r) == 2 and all(loc["full_name"] == "ОбщийМодуль.ТестОбщийМодуль" for loc in r)


def test_resolve_error_when_symbol_absent():
    class NoSym(FakeLsp):
        async def document_symbol(self, uri):
            return [{"name": "ДругойМетод", "selectionRange": {"start": {"line": 1, "character": 0}}}]
    try:
        asyncio.run(T.bsl_definition(_deps(lsp=NoSym()), "ОбщийМодуль.ТестОбщийМодуль.ВычислитьСумму"))
        assert False, "ожидали ResolveError"
    except T.ResolveError:
        pass


# ---------- сложность ----------
def test_complexity_pairs_metrics():
    r = asyncio.run(T.bsl_complexity(_deps(), "ОбщийМодуль.ТестОбщийМодуль"))
    assert r == [{"full_name": "ОбщийМодуль.ТестОбщийМодуль.ВычислитьСумму",
                  "cognitive": 7, "cyclomatic": 3}]


def test_complexity_function_filter():
    # фильтр по методу: несуществующий → пусто
    r = asyncio.run(T.bsl_complexity(_deps(), "ОбщийМодуль.ТестОбщийМодуль", function="НетТакого"))
    assert r == []


def test_complexity_rejects_bad_name():
    try:
        asyncio.run(T.bsl_complexity(_deps(), "ОдноСлово"))
        assert False, "ожидали ResolveError"
    except T.ResolveError:
        pass


# ---------- диагностики (через analyze-порт) ----------
def test_diagnostics_folds_below_threshold():
    raw = [
        {"range": {"start": {"line": 4}}, "code": "Deprecated", "severity": "Error",
         "message": "устаревшее", "_src_uri": MOD_URI},
        {"range": {"start": {"line": 9}}, "code": "Typo", "severity": "Info",
         "message": "опечатка", "_src_uri": MOD_URI},
    ]
    r = asyncio.run(T.bsl_diagnostics(_deps(analyzer=FakeAnalyzer(raw)),
                                      module_full_name="ОбщийМодуль.ТестОбщийМодуль"))
    # error показан целиком, info свёрнут в сводку (порог по умолчанию = warning)
    assert [d["code"] for d in r["diagnostics"]] == ["Deprecated"]
    assert r["diagnostics"][0]["severity"] == "error"
    assert r["diagnostics"][0]["file"] == "ОбщийМодуль.ТестОбщийМодуль"
    assert r["suppressed"]["total"] == 1
    assert r["suppressed"]["by_code"] == [{"code": "Typo", "severity": "info", "count": 1}]


def test_diagnostics_min_severity_shows_all():
    raw = [{"range": {"start": {"line": 0}}, "code": "Typo", "severity": "Info",
            "message": "m", "_src_uri": MOD_URI}]
    r = asyncio.run(T.bsl_diagnostics(_deps(analyzer=FakeAnalyzer(raw)),
                                      module_full_name="ОбщийМодуль.ТестОбщийМодуль",
                                      min_severity="hint"))
    assert [d["code"] for d in r["diagnostics"]] == ["Typo"] and r["suppressed"]["total"] == 0


def test_diagnostics_code_drilldown():
    raw = [{"range": {"start": {"line": 0}}, "code": "Typo", "severity": "Info",
            "message": "m", "_src_uri": MOD_URI},
           {"range": {"start": {"line": 1}}, "code": "Other", "severity": "Warning",
            "message": "m", "_src_uri": MOD_URI}]
    r = asyncio.run(T.bsl_diagnostics(_deps(analyzer=FakeAnalyzer(raw)),
                                      module_full_name="ОбщийМодуль.ТестОбщийМодуль", code="Typo"))
    assert [d["code"] for d in r["diagnostics"]] == ["Typo"] and r["suppressed"]["total"] == 0


def test_diagnostics_analyze_gets_module_dir():
    fa = FakeAnalyzer([])
    asyncio.run(T.bsl_diagnostics(_deps(analyzer=fa), module_full_name="ОбщийМодуль.ТестОбщийМодуль"))
    assert fa.seen_dir == (WS / "CommonModules" / "ТестОбщийМодуль" / "Ext").resolve()


# ---------- реиндекс ----------
def test_reindex_restarts_and_reports():
    lsp = FakeLsp()
    r = asyncio.run(T.bsl_reindex(T.Deps(lsp=lsp, analyzer=FakeAnalyzer([]), settings=_settings())))
    assert lsp.restarted is True and r["status"] == "reindexing"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
