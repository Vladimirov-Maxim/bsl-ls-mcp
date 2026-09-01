"""Точка входа MCP-сервера: выбор транспорта из настроек/CLI, плюс --selftest.
Используется консольным скриптом `bsl-ls-mcp`, `python -m bsl_ls_mcp`
и `scripts/run_mcp.py` (все делегируют сюда — DRY)."""
from __future__ import annotations

import argparse
import sys

from .settings import get_settings

# ВАЖНО: application.server НЕ импортируем на уровне модуля. Его тело создаёт _deps и
# вешает atexit -> lsp.kill(), а kill() пишет в ОБЩИЙ статус-файл. Из-за этого любой
# CLI-запуск (--reindex) при выходе затирал status работающего демона в idle: трей
# показывал «индекса нет», пока демон спокойно строил индекс. Импорт — только в
# демон-ветке main().


def main(argv: list[str] | None = None) -> None:
    s = get_settings()
    parser = argparse.ArgumentParser(
        prog="bsl-ls-mcp",
        description="MCP-сервер bsl-ls (stdio | sse | streamable-http) или --selftest.",
    )
    parser.add_argument(
        "--transport", choices=["stdio", "sse", "streamable-http"], default=s.mcp_transport,
        help="MCP transport: stdio (child-процесс) | sse (устар.) | streamable-http (актуальный сетевой).",
    )
    parser.add_argument("--host", default=s.mcp_host, help="Host для SSE (игнорируется при stdio).")
    parser.add_argument("--port", type=int, default=s.mcp_port, help="Port для SSE (игнорируется при stdio).")
    parser.add_argument(
        "--selftest", nargs="?", const="", default=None, metavar="ТИП.МОДУЛЬ",
        help="Самопроверка: поднять сервер, проиндексировать workspace и прогнать "
             "диагностики по модулю (если не задан — первый общий модуль). Печатает результат и выходит.",
    )
    parser.add_argument(
        "--reindex", action="store_true",
        help="Подключиться к УЖЕ запущенному локальному демону и запустить полный "
             "реиндекс (bsl_reindex), затем выйти. Для трея/скриптов.",
    )
    args = parser.parse_args(argv)

    if args.reindex:
        _reindex(s, args.host, args.port)
        return
    if args.selftest is not None:
        _selftest(args.selftest or None)
        return

    from .application.server import guard_bind_host, mcp  # только демон: тут создаются _deps и atexit

    if args.transport in ("sse", "streamable-http"):
        # ФАКТИЧЕСКИЙ адрес привязки — args.host (флаг переопределяет ENV), поэтому
        # охранник обязан проверять именно его: иначе `--host 0.0.0.0` обходит проверку,
        # сделанную при импорте только над ENV-host.
        guard_bind_host(args.host)
        # Демон: java поднимется лениво при первом вызове; агенты подключаются по URL.
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        path = "/sse" if args.transport == "sse" else "/mcp"
        print(f"[bsl-ls] {args.transport} на http://{args.host}:{args.port}{path}", file=sys.stderr)
        mcp.run(transport=args.transport)
    else:
        mcp.run(transport="stdio")


def _reindex(s, host: str, port: int) -> None:
    """MCP-клиент к локальному демону: вызвать bsl_reindex и выйти (для трея/скриптов)."""
    import asyncio
    from datetime import timedelta

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://{host}:{port}/mcp"

    async def run() -> int:
        try:
            async with streamablehttp_client(url) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    res = await session.call_tool(
                        "bsl_reindex", {}, read_timeout_seconds=timedelta(seconds=60))
                    print(f"[reindex] {getattr(res, 'structuredContent', None) or 'ok'}", file=sys.stderr)
                    return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[reindex] не удалось: демон на {url} недоступен? {exc}", file=sys.stderr)
            return 1

    sys.exit(asyncio.run(run()))


def _first_common_module(workspace) -> str | None:
    from pathlib import Path
    for p in Path(workspace).glob("CommonModules/*/Ext/Module.bsl"):
        return f"ОбщийМодуль.{p.parents[1].name}"  # CommonModules/<name>/Ext/Module.bsl
    return None


def _selftest(module: str | None) -> None:
    """Сквозная самопроверка бандла на ЭТОЙ машине: java + индексация + инструмент.
    Не требует внешнего MCP-клиента и Python на машине."""
    import asyncio
    import time

    from .application import tools
    from .application.tools import Deps
    from .bootstrap import build_analyzer, build_lsp

    s = get_settings()

    async def run() -> int:
        print(f"[selftest] workspace = {s.workspace}", file=sys.stderr)
        print(f"[selftest] java      = {s.java_path}", file=sys.stderr)
        print(f"[selftest] jar       = {s.jar_path}  (Xmx={s.xmx})", file=sys.stderr)
        mod = module or _first_common_module(s.workspace)
        if not mod:
            print("[selftest] FAIL: общий модуль не найден; задайте --selftest 'ОбщийМодуль.Имя'")
            return 1
        print(f"[selftest] модуль: {mod}", file=sys.stderr)
        print("[selftest] проверяю код модуля через analyze-CLI (без индекса, ~неск. секунд)...", file=sys.stderr)
        lsp = build_lsp(s)
        deps = Deps(lsp=lsp, analyzer=build_analyzer(s), settings=s)
        t = time.time()
        try:
            res = await tools.bsl_diagnostics(deps, mod)  # {diagnostics, suppressed} через analyze-CLI
        finally:
            await lsp.stop()
        diags = res["diagnostics"]
        suppressed = res["suppressed"]["total"]
        print(f"[selftest] {mod}: {len(diags)} error+warning (+{suppressed} info/hint свёрнуто) "
              f"за {time.time() - t:.0f}с")
        for d in diags[:5]:
            print("   ", d)
        print("[selftest] OK — обёртка, java и индексация работают на этой машине")
        return 0

    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
