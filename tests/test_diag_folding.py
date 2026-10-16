"""Сворачивание по размеру (BSL-1 п. 2): уровни по одному с низшего, error — никогда;
пометка называет свёрнутые уровни, их число и невыданный запрошенный уровень."""
import asyncio
import dataclasses

from test_tools_mock import WS, FakeAnalyzer, FakeLsp, _settings

from bsl_ls_mcp.application import tools as T

URI = (WS / "CommonModules" / "ТестОбщийМодуль" / "Ext" / "Module.bsl").resolve().as_uri()
SEV = {"error": "Error", "warning": "Warning", "info": "Information", "hint": "Hint"}


def _raw(**counts):
    out = []
    for level, n in counts.items():
        out += [{"range": {"start": {"line": i}}, "code": f"{level.title()}Код", "severity": SEV[level],
                 "message": "сообщение " * 3, "_src_uri": URI} for i in range(n)]
    return out


def _run(raw, budget, **kw):
    s = dataclasses.replace(_settings(), diag_engine="analyze", diag_max_chars=budget)
    return asyncio.run(T.bsl_diagnostics(T.Deps(lsp=FakeLsp(), analyzer=FakeAnalyzer(raw), settings=s),
                                         module_full_name="ОбщийМодуль.ТестОбщийМодуль", **kw))


def _levels(r):
    return sorted({d["severity"] for d in r["diagnostics"]})


def test_lowest_level_folds_first():
    r = _run(_raw(error=1, warning=2, info=3, hint=40), budget=1500, min_severity="hint")
    assert r["suppressed"]["folded_by_size"] == {"hint": 40}
    assert _levels(r) == ["error", "info", "warning"]
    note = r["suppressed"]["note"]
    assert "hint — 40" in note and "Запрошенный уровень hint выдан не целиком" in note
    assert "warning свёрнуты" not in note


def test_folds_up_to_warning_but_never_error():
    r = _run(_raw(error=5, warning=30), budget=200)
    assert r["suppressed"]["folded_by_size"] == {"warning": 30}
    assert _levels(r) == ["error"] and len(r["diagnostics"]) == 5
    assert "Запрошенный уровень warning выдан не целиком" in r["suppressed"]["note"]


def test_only_errors_over_budget_are_returned_as_is():
    r = _run(_raw(error=50), budget=100)
    assert len(r["diagnostics"]) == 50 and "note" not in r["suppressed"]


def test_no_note_when_it_fits():
    r = _run(_raw(error=1, warning=1, info=1), budget=30000, min_severity="hint")
    assert "note" not in r["suppressed"] and "folded_by_size" not in r["suppressed"]
    assert r["suppressed"]["total"] == 0


def test_threshold_hidden_is_not_reported_as_size_folding():
    # info/hint ниже порога по умолчанию — обычная сводка, без пометки о размере
    r = _run(_raw(warning=2, info=10), budget=30000)
    assert r["suppressed"]["total"] == 10 and "note" not in r["suppressed"]
