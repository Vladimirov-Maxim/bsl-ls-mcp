"""MCP-сервер (FastMCP). Единственный интерфейс наружу: mcp__bsl-ls__*.
Инструменты — тонкие обёртки над application.tools.

ВАЖНО: НЕ используем FastMCP lifespan для запуска LSP. Для сетевых транспортов
(streamable-http/sse) FastMCP вешает lifespan на lowlevel MCP-сервер, который
поднимается на КАЖДУЮ MCP-сессию → java стартовала бы и гасилась на каждое
подключение агента. Вместо этого держим ОДНУ общую LSP-сессию на весь демон:
java стартует лениво при первом вызове (_ensure_started в методах порта) и
остаётся тёплой между сессиями."""
from __future__ import annotations

import asyncio
import atexit
import os

from mcp.server.fastmcp import FastMCP

from ..bootstrap import build_analyzer, build_lsp
from ..settings import get_settings
from . import tools
from .tools import Deps

# Только эти адреса считаем безопасными по умолчанию. Пустая строка СЮДА НЕ ВХОДИТ:
# host='' биндит сокет на ВСЕ интерфейсы (0.0.0.0), а Host-валидация SDK при этом не
# включается (она только для 127.0.0.1/localhost/::1) — то есть хуже явного 0.0.0.0.
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def guard_bind_host(host: str) -> None:
    """Сервер НЕ аутентифицирует вызовы: любой, кто достучится до порта, получает все
    инструменты (обход каталога, чтение файлов, DoS реиндексом). Поэтому привязку к
    не-loopback интерфейсу требуем подтвердить явным BSL_ALLOW_REMOTE=1 — ни случайный
    `BSL_MCP_HOST=0.0.0.0`, ни флаг `--host 0.0.0.0` не должны молча выставить демон в
    сеть. ВНИМАНИЕ: Host-валидация SDK (anti-DNS-rebinding) НЕ аутентификация — заголовок
    Host тривиально подделывается прямым HTTP-клиентом; единственная реальная защита
    открытого эндпоинта — обратный прокси с авторизацией/TLS."""
    if host not in _LOOPBACK and os.environ.get("BSL_ALLOW_REMOTE") not in {"1", "true", "yes"}:
        raise RuntimeError(
            f"host={host!r} выставляет незащищённый MCP-эндпоинт в сеть. Демон не "
            "аутентифицирует вызовы — поставьте перед ним обратный прокси с авторизацией/TLS. "
            "Чтобы всё же слушать этот адрес, задайте BSL_ALLOW_REMOTE=1.")


# host/port сетевого транспорта — из настроек (инкапсулировано в Settings).
_s = get_settings()
guard_bind_host(_s.mcp_host)
mcp = FastMCP("bsl-ls", host=_s.mcp_host, port=_s.mcp_port)

# Очередь: ограничиваем число одновременных операций к единственной LSP-сессии.
_sem = asyncio.Semaphore(_s.max_concurrency)

# Навигация — одна живая LSP-сессия (тёплый индекс). Проверка кода — отдельный
# analyze-CLI (разовый java, своя очередь). Два пути, один jar (см. CodeAnalyzer).
_deps = Deps(lsp=build_lsp(_s), analyzer=build_analyzer(_s), settings=_s)

# При остановке демона — синхронно прибить java (иначе остаётся осиротевшим).
atexit.register(lambda: _deps.lsp.kill())


def _d() -> Deps:
    return _deps


@mcp.tool()
async def bsl_callers(full_name: str) -> list[dict]:
    """Кто вызывает функцию (incoming calls). full_name: 'ОбщийМодуль.МойМодуль.ИмяФункции'."""
    async with _sem:
        return await tools.bsl_callers(_d(), full_name)


@mcp.tool()
async def bsl_callees(full_name: str) -> list[dict]:
    """Кого вызывает функция (outgoing calls). full_name: 'ОбщийМодуль.МойМодуль.ИмяФункции'."""
    async with _sem:
        return await tools.bsl_callees(_d(), full_name)


@mcp.tool()
async def bsl_diagnostics(module_full_name: str | None = None, path: str | None = None,
                          text: str | None = None,
                          min_severity: str | None = None, code: str | None = None) -> dict:
    """Диагностики кода. Укажите РОВНО ОДИН адрес:
      module_full_name — модуль корпуса: 'ОбщийМодуль.МойМодуль' | 'Справочник.X.Форма.Имя';
      path — произвольный каталог или .bsl-файл ВНЕ корпуса (внешние обработки/отчёты),
             напр. 'C:\\1c\\work\\<задача>\\Реализация'. Индекс не нужен;
      text — СТРОКА кода 1С (снипет): линт до записи в файл. Обёртка в процедуру НЕ нужна
             (блок операторов линтуется как есть), нужна лишь синтаксическая завершённость
             (закрытые Если/Цикл, не оборванные выражения). Проверки ПОФАЙЛОВЫЕ, без
             контекста конфигурации (тип модуля/ссылки на объекты не проверяются).
             Для снипета по умолчанию возвращаются ВСЕ замечания (не только error+warning).

    По умолчанию для module/path отдаёт полным списком только error+warning, а info/hint
    сворачивает в сводку suppressed.by_code (счётчики по кодам) — чтобы не переполнять
    бюджет на крупных модулях/формах (для text порог по умолчанию 'hint' — всё). Детали:
    code='Typo' (все по коду, любой severity) или min_severity='hint' (всё подряд).
    Возвращает {"diagnostics":[...], "suppressed":{"total":N,"by_code":[...]}}.
    Идёт через analyze-CLI (без тёплого индекса) со своей очередью — навигационный
    семафор тут не нужен, проверка кода не конкурирует за граф."""
    return await tools.bsl_diagnostics(_d(), module_full_name, path, text, min_severity, code)


@mcp.tool()
async def bsl_definition(full_name: str) -> list[dict]:
    """Где объявлен символ. full_name: 'ОбщийМодуль.МойМодуль.ИмяФункции'."""
    async with _sem:
        return await tools.bsl_definition(_d(), full_name)


@mcp.tool()
async def bsl_references(full_name: str) -> list[dict]:
    """Где используется символ. full_name: 'ОбщийМодуль.МойМодуль.ИмяФункции'."""
    async with _sem:
        return await tools.bsl_references(_d(), full_name)


@mcp.tool()
async def bsl_complexity(module_full_name: str, function: str | None = None) -> list[dict]:
    """Сложность методов модуля (когнитивная + цикломатическая). Метрика ревьюеру.
    module_full_name: 'ОбщийМодуль.МойМодуль'; function (опц.) — только этот метод."""
    async with _sem:
        return await tools.bsl_complexity(_d(), module_full_name, function)


@mcp.tool()
async def bsl_reindex() -> dict:
    """Полный реиндекс корпуса (после массовых изменений конфигурации).
    Точечные правки модулей подхватываются автоматически — это для крупных изменений."""
    async with _sem:
        return await tools.bsl_reindex(_d())
