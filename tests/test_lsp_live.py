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
            assert r["источник"] == {"движок": "lsp"}
            codes = {(d["code"], d["line"]) for d in r["diagnostics"]}
            assert ("MissingCommonModuleMethod", 3) in codes          # несуществующий метод виден
            assert ("UnknownMember", 2) not in codes, codes            # существующий метод — не ложный
            assert not any(c == "CommentedCode" for c, _ in codes), codes  # конфиг применён и в LSP

            # повтор по неизменённому модулю — снова LSP и тот же ответ (не ожидание публикации)
            again = await tools.bsl_diagnostics(deps, module_full_name="ОбщийМодуль.Проверка", min_severity="hint")
            assert again["источник"] == {"движок": "lsp"}
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
