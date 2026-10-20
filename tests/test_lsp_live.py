"""Живой BSL LS на маленьком EDT-workspace (~20 c): свежесть графа вызовов после правки
файла на диске (ЗИ-13) и диагностики из тёплого LSP с контекстом конфигурации.
Нужна Java 21 и jar BSL LS: BSL_JAVA или jre/ рядом с проектом; иначе тест пропускается."""
from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
import uuid
from pathlib import Path

import pytest

from bsl_ls_mcp.application import tools
from bsl_ls_mcp.infrastructure.stdio_lsp_client import StdioLspClient
from bsl_ls_mcp.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]
_JAVA = os.environ.get("BSL_JAVA") or str(ROOT / "jre" / "bin" / "java.exe")
_JAR = get_settings().jar_path
pytestmark = pytest.mark.skipif(not (Path(_JAVA).is_file() and _JAR.is_file()),
                                reason="нет Java 21 (BSL_JAVA / jre) или jar BSL LS")
NS = 'xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass"'


def _workspace(root: Path) -> Path:
    ws = root / "src"
    mods = {"Поставщик": "Процедура Метод() Экспорт\nКонецПроцедуры\n",
            "Потребитель": "Процедура Пусто() Экспорт\nКонецПроцедуры\n",
            "Проверка": ("Процедура Проверить() Экспорт\n\tПоставщик.Метод();\n"
                         "\tПоставщик.НетТакогоМетода();\n\t//А = 1;\n\t//Б = А + 2;\nКонецПроцедуры\n")}
    (ws / "Configuration").mkdir(parents=True)
    listed = "".join(f"  <commonModules>CommonModule.{m}</commonModules>\n" for m in mods)
    (ws / "Configuration" / "Configuration.mdo").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<mdclass:Configuration {NS} uuid="{uuid.uuid4()}">\n'
        f"  <name>Проба</name>\n  <scriptVariant>Russian</scriptVariant>\n{listed}</mdclass:Configuration>\n",
        encoding="utf-8")
    for name, body in mods.items():
        d = ws / "CommonModules" / name
        d.mkdir(parents=True)
        (d / f"{name}.mdo").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<mdclass:CommonModule {NS} uuid="{uuid.uuid4()}">\n'
            f"  <name>{name}</name>\n  <server>true</server>\n</mdclass:CommonModule>\n", encoding="utf-8")
        (d / "Module.bsl").write_text(body, encoding="utf-8")
    return ws


def test_live_callers_fresh_after_disk_edit_and_lsp_diagnostics(tmp_path):
    ws = _workspace(tmp_path)
    cfg = tmp_path / "bsl-config.json"          # конфиг проекта: CommentedCode выключен
    cfg.write_text('{"diagnostics": {"mode": "EXCEPT", "parameters": {"CommentedCode": false}}}',
                   encoding="utf-8")
    s = dataclasses.replace(get_settings(), workspace=ws, allowed_roots=(ws,), java_path=_JAVA, xmx="2g",
                            status_file=tmp_path / "status.json", fresh_min_interval=0, fresh_scan_sec=0,
                            diag_engine="auto",
                            bsl_config=cfg)
    lsp = StdioLspClient(s)
    deps = tools.Deps(lsp=lsp, analyzer=None, settings=s)

    async def scenario():
        try:
            callers = await tools.bsl_callers(deps, "ОбщийМодуль.Поставщик.Метод")
            assert [c["full_name"] for c in callers] == ["ОбщийМодуль.Проверка.Проверить"]

            # правка на диске без всякого уведомления со стороны агента
            (ws / "CommonModules" / "Потребитель" / "Module.bsl").write_text(
                "Процедура Пусто() Экспорт\n\tПоставщик.Метод();\nКонецПроцедуры\n", encoding="utf-8")
            callers = await tools.bsl_callers(deps, "ОбщийМодуль.Поставщик.Метод")
            assert sorted(c["full_name"] for c in callers) == [
                "ОбщийМодуль.Потребитель.Пусто", "ОбщийМодуль.Проверка.Проверить"]

            assert lsp.is_ready()
            r = await tools.bsl_diagnostics(deps, module_full_name="ОбщийМодуль.Проверка", min_severity="hint")
            assert r["источник"]["движок"] == "lsp"
            codes = {(d["code"], d["line"]) for d in r["diagnostics"]}
            assert ("MissingCommonModuleMethod", 3) in codes          # несуществующий метод виден
            assert ("UnknownMember", 2) not in codes, codes            # существующий метод — не ложный
            assert not any(c == "CommentedCode" for c, _ in codes), codes  # конфиг применён и в LSP

            # повтор по неизменённому модулю — снова LSP и тот же ответ (не ожидание публикации)
            again = await tools.bsl_diagnostics(deps, module_full_name="ОбщийМодуль.Проверка", min_severity="hint")
            assert again["источник"]["движок"] == "lsp"
            assert {(d["code"], d["line"]) for d in again["diagnostics"]} == codes

            # правка на диске — ответ на новый текст
            mod = ws / "CommonModules" / "Проверка" / "Module.bsl"
            mod.write_text(mod.read_text(encoding="utf-8").replace("КонецПроцедуры", "\tВ = 5;\nКонецПроцедуры"),
                           encoding="utf-8")
            edited = await tools.bsl_diagnostics(deps, module_full_name="ОбщийМодуль.Проверка", min_severity="hint")
            assert ("UnusedLocalVariable", 6) in {(d["code"], d["line"]) for d in edited["diagnostics"]}

            # модуль, созданный после индексации: вызов видят и граф, и диагностики, с оговоркой
            new = ws / "CommonModules" / "Новый"
            new.mkdir()
            (new / "Новый.mdo").write_text(
                f'<?xml version="1.0" encoding="UTF-8"?>\n<mdclass:CommonModule {NS} uuid="{uuid.uuid4()}">\n'
                "  <name>Новый</name>\n  <server>true</server>\n</mdclass:CommonModule>\n", encoding="utf-8")
            (new / "Module.bsl").write_text(
                "Процедура Н() Экспорт\n\tПоставщик.Метод();\n\tПоставщик.ЕщёНетМетода();\nКонецПроцедуры\n",
                encoding="utf-8")
            callers = await tools.bsl_callers(deps, "ОбщийМодуль.Поставщик.Метод")
            assert "ОбщийМодуль.Новый.Н" in {c["full_name"] for c in callers}
            fresh = await tools.bsl_diagnostics(deps, module_full_name="ОбщийМодуль.Новый", min_severity="hint")
            assert fresh["источник"]["движок"] == "lsp" and "после индексации" in fresh["источник"]["примечание"]
            assert ("MissingCommonModuleMethod", 3) in {(d["code"], d["line"]) for d in fresh["diagnostics"]}
        finally:
            await lsp.stop()

    try:
        asyncio.run(scenario())
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def test_live_complexity_outside_workspace_leaves_no_trace(tmp_path):
    """bsl_complexity(path=…) вне workspace (ЗИ-17): числа из линз, а документ после запроса
    не остаётся в графе вызовов (одного didClose BSL LS мало)."""
    ws = _workspace(tmp_path)
    outside = tmp_path / "ревизия" / "Module.bsl"
    outside.parent.mkdir()
    outside.write_text("Процедура Внешний() Экспорт\n\tЕсли Истина Тогда\n\t\tПоставщик.Метод();\n"
                       "\tКонецЕсли;\nКонецПроцедуры\n", encoding="utf-8")
    s = dataclasses.replace(get_settings(), workspace=ws, allowed_roots=(ws,), java_path=_JAVA, xmx="2g",
                            status_file=tmp_path / "status.json", fresh_min_interval=0, fresh_scan_sec=0)
    lsp = StdioLspClient(s)
    deps = tools.Deps(lsp=lsp, analyzer=None, settings=s)

    async def scenario():
        try:
            r = await tools.bsl_complexity(deps, path=str(outside.parent))
            assert r == [{"full_name": "Module.Внешний", "cognitive": 1, "cyclomatic": 2}]
            callers = await tools.bsl_callers(deps, "ОбщийМодуль.Поставщик.Метод")
            assert [c["full_name"] for c in callers] == ["ОбщийМодуль.Проверка.Проверить"]
        finally:
            await lsp.stop()

    try:
        asyncio.run(scenario())
    finally:
        shutil.rmtree(ws, ignore_errors=True)


_MD = ('<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:v8="http://v8.1c.ru/8.1/data/core" '
       'xmlns:xs="http://www.w3.org/2001/XMLSchema" version="2.17">')


def test_live_external_processing_form_attributes_visible(tmp_path):
    """Внешняя обработка вне workspace (ЗИ-70): без описания формы реквизит `Счетчик` —
    «неиспользуемая локальная переменная», обработчик `ПриОткрытии` — «неиспользуемый метод»;
    в обёртке-конфигурации BSL LS читает Form.xml и ложных замечаний нет."""
    from bsl_ls_mcp.infrastructure.analyze_cli import AnalyzeCliRunner

    root = tmp_path / "Реализация"
    obj = root / "Обр"
    (obj / "Ext").mkdir(parents=True)
    (root / "Обр.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n{_MD}\n<ExternalDataProcessor uuid="{uuid.uuid4()}">\n'
        "<Properties><Name>Обр</Name><DefaultForm>ExternalDataProcessor.Обр.Form.Форма</DefaultForm></Properties>\n"
        "<ChildObjects><Form>Форма</Form></ChildObjects>\n</ExternalDataProcessor>\n</MetaDataObject>\n",
        encoding="utf-8")
    (obj / "Ext" / "ObjectModule.bsl").write_text("Процедура П() Экспорт\nКонецПроцедуры\n", encoding="utf-8")
    form_dir = obj / "Forms" / "Форма" / "Ext"
    (form_dir / "Form").mkdir(parents=True)
    (obj / "Forms" / "Форма.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n{_MD}\n<Form uuid="{uuid.uuid4()}">\n'
        "<Properties><Name>Форма</Name><FormType>Managed</FormType></Properties>\n</Form>\n</MetaDataObject>\n",
        encoding="utf-8")
    (form_dir / "Form.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<Form xmlns="http://v8.1c.ru/8.3/xcf/logform" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xs="http://www.w3.org/2001/XMLSchema" version="2.17">\n'
        '<Events><Event name="OnOpen">ПриОткрытии</Event></Events>\n<Attributes><Attribute name="Счетчик" id="1">'
        "<Type><v8:Type>xs:decimal</v8:Type></Type></Attribute></Attributes>\n</Form>\n", encoding="utf-8")
    module = form_dir / "Form" / "Module.bsl"
    module.write_text("&НаКлиенте\nПроцедура ПриОткрытии(Отказ)\n\tСчетчик = 1;\nКонецПроцедуры\n", encoding="utf-8")
    s = dataclasses.replace(get_settings(), java_path=_JAVA)
    deps = tools.Deps(lsp=None, analyzer=AnalyzeCliRunner(s), settings=s)

    bare = {(d["code"], d["range"]["start"]["line"] + 1) for d in asyncio.run(deps.analyzer.analyze(module.parent))}
    assert {("UnusedLocalVariable", 3), ("UnusedLocalMethod", 2)} <= bare      # ЗИ-70 воспроизводится
    r = asyncio.run(tools.bsl_diagnostics(deps, path=str(module), min_severity="hint"))
    got = {(d["code"], d["line"]) for d in r["diagnostics"]}
    assert not any(c.startswith("UnusedLocal") for c, _ in got), got
    assert {d["file"] for d in r["diagnostics"]} <= {"ВнешняяОбработка.Обр.Форма.Форма"}
    assert "обёртке-конфигурации" in r["источник"]["примечание"]


def test_live_cli_diagnostics_task_lines_with_form_context(tmp_path):
    """`bsl-ls-mcp diagnostics` (BSL-8) на настоящем BSL LS: проект EDT из модулей задачи и описаний —
    реквизит формы не «неиспользуемая переменная», тип общего модуля известен
    (ExecuteExternalCodeInCommonModule), находки только на строках задачи."""
    import json
    import subprocess
    import sys

    from checks_support import commit, git, write

    repo = tmp_path / "edt"
    repo.mkdir()
    git(repo, "init", "-q")
    src = "App/src/"
    write(repo, "App/DT-INF/PROJECT.PMF", ["Manifest-Version: 1.0", "Runtime-Version: 8.3.24"], eol="\n")
    write(repo, src + "Configuration/Configuration.mdo", [
        '<?xml version="1.0" encoding="UTF-8"?>', f'<mdclass:Configuration {NS} uuid="{uuid.uuid4()}">',
        "  <name>Проба</name>", "  <scriptVariant>Russian</scriptVariant>",
        "  <commonModules>CommonModule.Серв</commonModules>", "  <catalogs>Catalog.Спр</catalogs>",
        "</mdclass:Configuration>"], eol="\n")
    write(repo, src + "CommonModules/Серв/Серв.mdo", [
        '<?xml version="1.0" encoding="UTF-8"?>', f'<mdclass:CommonModule {NS} uuid="{uuid.uuid4()}">',
        "  <name>Серв</name>", "  <server>true</server>", "</mdclass:CommonModule>"], eol="\n")
    write(repo, src + "CommonModules/Серв/Module.bsl", ["Процедура П(Текст) Экспорт", "КонецПроцедуры"], eol="\n")
    write(repo, src + "Catalogs/Спр/Спр.mdo", [
        '<?xml version="1.0" encoding="UTF-8"?>', f'<mdclass:Catalog {NS} uuid="{uuid.uuid4()}">', "  <name>Спр</name>",
        f'  <forms uuid="{uuid.uuid4()}">', "    <name>ФормаЭлемента</name>", "  </forms>", "</mdclass:Catalog>"], eol="\n")
    write(repo, src + "Catalogs/Спр/Forms/ФормаЭлемента/Form.form", [
        '<?xml version="1.0" encoding="UTF-8"?>', '<form:Form xmlns:form="http://g5.1c.ru/v8/dt/form">',
        "  <handlers>", "    <event>OnOpen</event>", "    <name>ПриОткрытии</name>", "  </handlers>",
        "  <attributes>", "    <name>Счетчик</name>", "    <id>1</id>", "  </attributes>", "</form:Form>"], eol="\n")
    form = src + "Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl"
    write(repo, form, ["&НаКлиенте", "Процедура Пусто()", "КонецПроцедуры"], eol="\n")
    commit(repo)
    write(repo, src + "CommonModules/Серв/Module.bsl",
          ["Процедура П(Текст) Экспорт", "\tВыполнить(Текст);", "КонецПроцедуры"], eol="\n")
    write(repo, form, ["&НаКлиенте", "Процедура Пусто()", "КонецПроцедуры", "",
                       "&НаКлиенте", "Процедура ПриОткрытии(Отказ)", "\tСчетчик = 1;", "КонецПроцедуры"], eol="\n")
    out = tmp_path / "диагностики.json"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8", "BSL_JAVA": _JAVA}
    r = subprocess.run([sys.executable, "-m", "bsl_ls_mcp", "diagnostics", "--repo", str(repo), "--base", "HEAD",
                        "--out", str(out)], capture_output=True, env=env, timeout=600)
    assert r.returncode in (0, 1), r.stderr.decode("utf-8", "replace")
    result = json.loads(out.read_text(encoding="utf-8"))
    got = {(f["ид"], f["файл"], f["строка"]) for f in result["находки"]}
    assert result["пропущено"] == []
    assert ("ExecuteExternalCodeInCommonModule", src + "CommonModules/Серв/Module.bsl", 2) in got, got
    assert not any(code.startswith("UnusedLocal") for code, _, _ in got), got
    assert all(line in ((2,) if "Серв" in file else (5, 6, 7, 8)) for _, file, line in got), got


def test_live_external_processing_edt_form_attributes_visible(tmp_path):
    """То же для внешней обработки EDT (ЗИ-70): без DT-INF/PROJECT.PMF analyze раскладку EDT не
    распознаёт — обёртка 0.12.0 его не клала, и ложные Unused* оставались."""
    from bsl_ls_mcp.infrastructure.analyze_cli import AnalyzeCliRunner

    obj = tmp_path / "proj" / "src" / "ExternalDataProcessors" / "Обр"
    (obj / "Forms" / "Форма").mkdir(parents=True)
    (obj / "Обр.mdo").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<mdclass:ExternalDataProcessor {NS} uuid="{uuid.uuid4()}">\n'
        f'  <name>Обр</name>\n  <forms uuid="{uuid.uuid4()}">\n    <name>Форма</name>\n  </forms>\n'
        "</mdclass:ExternalDataProcessor>\n", encoding="utf-8")
    (obj / "ObjectModule.bsl").write_text("Процедура П() Экспорт\nКонецПроцедуры\n", encoding="utf-8")
    (obj / "Forms" / "Форма" / "Form.form").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<form:Form xmlns:form="http://g5.1c.ru/v8/dt/form">\n'
        "  <handlers>\n    <event>OnOpen</event>\n    <name>ПриОткрытии</name>\n  </handlers>\n"
        "  <attributes>\n    <name>Счетчик</name>\n    <id>1</id>\n  </attributes>\n</form:Form>\n", encoding="utf-8")
    module = obj / "Forms" / "Форма" / "Module.bsl"
    module.write_text("&НаКлиенте\nПроцедура ПриОткрытии(Отказ)\n\tСчетчик = 1;\nКонецПроцедуры\n", encoding="utf-8")
    s = dataclasses.replace(get_settings(), java_path=_JAVA)
    deps = tools.Deps(lsp=None, analyzer=AnalyzeCliRunner(s), settings=s)

    bare = {d["code"] for d in asyncio.run(deps.analyzer.analyze(module.parent))}
    assert {"UnusedLocalVariable", "UnusedLocalMethod"} <= bare
    r = asyncio.run(tools.bsl_diagnostics(deps, path=str(module), min_severity="hint"))
    assert not any(d["code"].startswith("UnusedLocal") for d in r["diagnostics"]), r["diagnostics"]
    assert {d["file"] for d in r["diagnostics"]} <= {"ВнешняяОбработка.Обр.Форма.Форма"}
