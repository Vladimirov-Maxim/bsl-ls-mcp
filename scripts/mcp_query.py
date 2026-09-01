"""Боевой MCP-клиент: подключается к демону bsl-ls по streamable-http и вызывает
инструмент — как это сделал бы агент. Показывает СЫРОЙ ответ MCP.
Запуск: py -3 scripts/mcp_query.py [tool] [full_name]"""
import asyncio
import json
import os
import sys
from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = os.environ.get("MCP_URL", "http://127.0.0.1:8081/mcp")
TOOL = sys.argv[1] if len(sys.argv) > 1 else "bsl_callers"
FULL_NAME = sys.argv[2] if len(sys.argv) > 2 else \
    "ОбщийМодуль.ОбщегоНазначения.ЗначениеРеквизитаОбъекта"


async def main():
    print(f"[mcp] connect {URL}")
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("[mcp] tools:", [t.name for t in tools.tools])

            print(f"\n[mcp] call {TOOL}(full_name={FULL_NAME!r})")
            res = await session.call_tool(
                TOOL, {"full_name": FULL_NAME},
                read_timeout_seconds=timedelta(seconds=180),  # покрывает холодную индексацию
            )
            print(f"[mcp] isError={res.isError}")
            sc = getattr(res, "structuredContent", None)
            if sc is not None:
                print("[mcp] structuredContent:")
                print(json.dumps(sc, ensure_ascii=False, indent=2))
            for c in res.content:
                if getattr(c, "type", "") == "text":
                    print("[mcp] text content:")
                    print(c.text)


if __name__ == "__main__":
    asyncio.run(main())
