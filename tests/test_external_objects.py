"""Внешняя обработка вне workspace: обёртка во временную конфигурацию, чтобы BSL LS
видел описание форм; и сложность по path — в корпусе, во внешнем объекте и вне всего."""
import asyncio
import dataclasses
from pathlib import Path

from test_tools_mock import FakeAnalyzer, FakeLsp, _settings

from bsl_ls_mcp.application import external_wrap
from bsl_ls_mcp.application import tools as T
from bsl_ls_mcp.domain import external_objects as eo
from bsl_ls_mcp.infrastructure.analyze_cli import _source_uri

HEAD = ('<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" version="2.17">')


def _designer(root: Path, name="Обр") -> Path:
    obj = root / name
    (obj / "Ext").mkdir(parents=True)
    (root / f"{name}.xml").write_text(
        f'\ufeff<?xml version="1.0" encoding="UTF-8"?>\n{HEAD}\n<ExternalDataProcessor uuid="1">\n'
        f"<Properties><Name>{name}</Name><DefaultForm>ExternalDataProcessor.{name}.Form.Форма</DefaultForm>"
        "</Properties>\n<ChildObjects><Form>Форма</Form></ChildObjects>\n</ExternalDataProcessor>\n</MetaDataObject>\n",
        encoding="utf-8")
    (obj / "Ext" / "ObjectModule.bsl").write_text("Процедура П() Экспорт\nКонецПроцедуры\n", encoding="utf-8")
    form = obj / "Forms" / "Форма" / "Ext"
    (form / "Form").mkdir(parents=True)
    (obj / "Forms" / "Форма.xml").write_text(f"{HEAD}<Form><Properties><Name>Форма</Name></Properties></Form>"
                                             "</MetaDataObject>", encoding="utf-8")
    (form / "Form.xml").write_text(f'<Form><Attributes><Attribute name="Объект"><Type><v8:Type>'
                                   f"cfg:ExternalDataProcessorObject.{name}</v8:Type></Type></Attribute>"
                                   "</Attributes></Form>", encoding="utf-8")
    (form / "Form" / "Module.bsl").write_text("&НаКлиенте\nПроцедура Ф()\nКонецПроцедуры\n", encoding="utf-8")
    return obj


def test_kinds_and_labels():
    k = eo.kind_of_designer(f"{HEAD}\n<ExternalReport uuid='1'>")
    assert (k.kind, k.edt, k.config_kind, k.config_dir, k.type_ru) == (
        "ExternalReport", False, "Report", "Reports", "ВнешнийОтчет")
    assert eo.kind_of_designer(f"{HEAD}\n<DataProcessor uuid='1'>") is None
    assert eo.kind_of_edt('<mdclass:ExternalDataProcessor xmlns:mdclass="x" uuid="1">').config_kind == "DataProcessor"
    assert eo.as_config_object("<types>cfg:ExternalDataProcessorObject.Обр</types> ExternalReport.О.Form.Ф") == (
        "<types>cfg:DataProcessorObject.Обр</types> Report.О.Form.Ф")
    k = eo.ExternalKind("ExternalDataProcessor", edt=False)
    assert eo.module_label(k, "Обр", ("Ext", "ObjectModule.bsl")) == "ВнешняяОбработка.Обр.МодульОбъекта"
    assert eo.module_label(k, "Обр", ("ObjectModule.bsl",)) == "ВнешняяОбработка.Обр.МодульОбъекта"
    assert eo.module_label(k, "Обр", ("Forms", "Ф", "Ext", "Form", "Module.bsl")) == "ВнешняяОбработка.Обр.Форма.Ф"
    assert eo.module_label(k, "Обр", ("Forms", "Ф", "Module.bsl")) == "ВнешняяОбработка.Обр.Форма.Ф"
    assert eo.module_label(k, "Обр", ("Templates", "М", "Ext", "Template.bsl")) is None


def test_find_and_wrap_designer(tmp_path):
    obj = _designer(tmp_path / "Реализация")
    for p in (tmp_path / "Реализация", obj, obj / "Forms", obj / "Forms" / "Форма" / "Ext" / "Form" / "Module.bsl"):
        ext = external_wrap.find_external(p)
        assert (ext.name, ext.object_dir) == ("Обр", obj.resolve()), p
    assert external_wrap.find_external(tmp_path) is None

    out = tmp_path / "wrap"
    out.mkdir()
    mirror = external_wrap.wrap(external_wrap.find_external(obj), out)
    cfg = (out / "Configuration.xml").read_text(encoding="utf-8")
    assert "<DataProcessor>Обр</DataProcessor>" in cfg and HEAD in cfg
    root = (out / "DataProcessors" / "Обр.xml").read_text(encoding="utf-8")
    assert "<DataProcessor uuid" in root and "DataProcessor.Обр.Form.Форма" in root and "External" not in root
    form = (out / "DataProcessors" / "Обр" / "Forms" / "Форма" / "Ext" / "Form.xml").read_text(encoding="utf-8")
    assert "cfg:DataProcessorObject.Обр" in form
    assert sorted(Path(k).name for k in mirror) == ["module.bsl", "objectmodule.bsl"]
    assert all(v.startswith("file:") for v in mirror.values())


def test_find_and_wrap_edt(tmp_path):
    obj = tmp_path / "proj" / "src" / "ExternalDataProcessors" / "Обр"
    (obj / "Forms" / "Форма").mkdir(parents=True)
    (obj / "Обр.mdo").write_text('<mdclass:ExternalDataProcessor xmlns:mdclass="x" uuid="1"><name>Обр</name>'
                                 "</mdclass:ExternalDataProcessor>", encoding="utf-8")
    (obj / "ObjectModule.bsl").write_text("Процедура П()\nКонецПроцедуры\n", encoding="utf-8")
    (obj / "Forms" / "Форма" / "Form.form").write_text("<types>ExternalDataProcessorObject.Обр</types>",
                                                         encoding="utf-8")
    (obj / "Forms" / "Форма" / "Module.bsl").write_text("Процедура Ф()\nКонецПроцедуры\n", encoding="utf-8")
    ext = external_wrap.find_external(obj / "Forms" / "Форма" / "Module.bsl")
    assert ext.kind.edt and ext.name == "Обр"
    assert ext.label(obj / "Forms" / "Форма" / "Module.bsl") == "ВнешняяОбработка.Обр.Форма.Форма"
    out = tmp_path / "wrap"
    out.mkdir()
    external_wrap.wrap(ext, out)
    assert (out / "DT-INF" / "PROJECT.PMF").is_file()          # без него analyze не видит проект EDT
    assert "<dataProcessors>DataProcessor.Обр</dataProcessors>" in (
        out / "src" / "Configuration" / "Configuration.mdo").read_text(encoding="utf-8")
    obj_out = out / "src" / "DataProcessors" / "Обр"
    assert "<mdclass:DataProcessor " in (obj_out / "Обр.mdo").read_text(encoding="utf-8")
    assert "DataProcessorObject.Обр" in (obj_out / "Forms" / "Форма" / "Form.form").read_text(encoding="utf-8")


class WrapAnalyzer(FakeAnalyzer):
    """analyze по обёртке: проверяет её состав и отвечает диагностиками копий модулей."""

    def __init__(self):
        super().__init__([])
        self.config_seen = False

    async def analyze(self, src_dir):
        self.seen_dir = src_dir
        self.config_seen = (Path(src_dir) / "Configuration.xml").is_file()
        obj = Path(src_dir) / "DataProcessors" / "Обр"
        return [{"range": {"start": {"line": 1}}, "code": c, "severity": "Warning", "message": "m",
                 "_src_uri": (obj / rel).resolve().as_uri()}
                for c, rel in (("A", "Ext/ObjectModule.bsl"), ("B", "Forms/Форма/Ext/Form/Module.bsl"))]


def test_diagnostics_external_processing_wrapped(tmp_path):
    obj = _designer(tmp_path / "Реализация")
    deps = T.Deps(lsp=FakeLsp(), analyzer=WrapAnalyzer(), settings=_settings())
    form = obj / "Forms" / "Форма" / "Ext" / "Form" / "Module.bsl"
    r = asyncio.run(T.bsl_diagnostics(deps, path=str(form)))
    assert deps.analyzer.config_seen
    assert [(d["code"], d["file"]) for d in r["diagnostics"]] == [("B", "ВнешняяОбработка.Обр.Форма.Форма")]
    assert "обёртке-конфигурации" in r["источник"]["примечание"]
    r = asyncio.run(T.bsl_diagnostics(deps, path=str(tmp_path / "Реализация")))
    assert sorted((d["code"], d["file"]) for d in r["diagnostics"]) == [
        ("A", "ВнешняяОбработка.Обр.МодульОбъекта"), ("B", "ВнешняяОбработка.Обр.Форма.Форма")]
    assert not Path(deps.analyzer.seen_dir).exists()                # обёртка убрана


def test_analyze_source_uri_prefers_file():
    assert _source_uri({"mdoRef": "file:///C:/x/Module.bsl", "path": "file:///other"}) == "file:///C:/x/Module.bsl"
    assert _source_uri({"mdoRef": "DataProcessor.Обр.Form.Ф", "path": "file:///C:/w/M.bsl"}) == "file:///C:/w/M.bsl"


class ClosingLsp(FakeLsp):
    def __init__(self):
        super().__init__()
        self.closed = []

    async def close(self, uri, forget=False):
        self.closed.append((Path(uri).name, forget))


def test_complexity_by_path(tmp_path):
    lsp = ClosingLsp()
    deps = T.Deps(lsp=lsp, analyzer=FakeAnalyzer([]), settings=_settings())
    ws_mod = _settings().workspace / "CommonModules" / "ТестОбщийМодуль" / "Ext" / "Module.bsl"
    r = asyncio.run(T.bsl_complexity(deps, path=str(ws_mod)))
    assert r == [{"full_name": "ОбщийМодуль.ТестОбщийМодуль.ВычислитьСумму", "cognitive": 7, "cyclomatic": 3}]
    assert lsp.closed == []                                          # корпус: документ остаётся открытым

    obj = _designer(tmp_path / "Реализация")
    r = asyncio.run(T.bsl_complexity(deps, path=str(obj)))
    assert sorted(x["full_name"] for x in r) == ["ВнешняяОбработка.Обр.МодульОбъекта.ВычислитьСумму",
                                                 "ВнешняяОбработка.Обр.Форма.Форма.ВычислитьСумму"]
    assert sorted(lsp.closed) == [("Module.bsl", True), ("ObjectModule.bsl", True)]

    loose = tmp_path / "ревизия" / "CommonModules" / "Х" / "Ext"
    loose.mkdir(parents=True)
    (loose / "Module.bsl").write_text("Процедура П()\nКонецПроцедуры\n", encoding="utf-8")
    r = asyncio.run(T.bsl_complexity(deps, path=str(tmp_path / "ревизия"), function="вычислитьсумму"))
    assert [x["full_name"] for x in r] == ["CommonModules/Х/Ext/Module.ВычислитьСумму"]


def test_complexity_address_errors(tmp_path):
    deps = T.Deps(lsp=ClosingLsp(), analyzer=FakeAnalyzer([]), settings=_settings())
    for kw in ({}, {"module_full_name": "ОбщийМодуль.Х", "path": str(tmp_path)}):
        try:
            asyncio.run(T.bsl_complexity(deps, **kw))
        except T.ResolveError as exc:
            assert "РОВНО ОДИН" in str(exc)
        else:
            raise AssertionError(kw)
    try:
        asyncio.run(T.bsl_complexity(deps, path=str(tmp_path)))
    except T.ResolveError as exc:
        assert "нет .bsl" in str(exc)
    small = dataclasses.replace(_settings(), diag_lsp_max_files=1)
    many = tmp_path / "много"
    many.mkdir()
    for i in range(2):
        (many / f"{i}.bsl").write_text("", encoding="utf-8")
    try:
        asyncio.run(T.bsl_complexity(T.Deps(lsp=ClosingLsp(), analyzer=None, settings=small), path=str(many)))
    except T.ResolveError as exc:
        assert "больше 1" in str(exc)
