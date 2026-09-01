"""Единая точка конфигурации обёртки — все параметры здесь, управляются через ENV.
(Единый источник настроек через ENV.)

Группы: источник/пути · JVM · MCP-транспорт (адрес/порт) · тайминги.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # --- источник и пути ---
    jar_path: Path           # исполняемый jar BSL LS (pin версии — в server/)
    workspace: Path          # исходники базы 1С (рабочая копия, её индексирует сервер)
    bsl_config: Path | None  # путь к .bsl-language-server.json, необязателен
    server_log: Path | None  # куда писать stderr java-сервера (для диагностики); None — отбрасывать
    status_file: Path        # файл состояния индекса (idle/building/ready) — читает трей

    # --- JVM ---
    java_path: str           # путь к java (для portable JRE в бандле); дефолт "java" из PATH
    xmx: str                 # память JVM. ВАЖНО: live-set индекса корпуса ~12.6 ГБ;
                             # Xmx МЕНЬШЕ live-set → вечный GC и зависание индексации.
                             # Дефолт 14g (с запасом); на больших конфигах поднимать.

    # --- MCP-транспорт (адрес/порт) ---
    mcp_transport: str       # "stdio" | "sse"
    mcp_host: str            # host для sse (игнорируется при stdio)
    mcp_port: int            # port для sse
    max_concurrency: int     # макс. одновременных операций к единственной LSP-сессии
    diag_max_chars: int      # бюджет размера списка диагностик (символы); сверх —
                             # warning сворачиваются в сводку (error всегда целиком)

    # --- проверка кода (analyze-CLI, отдельно от тёплого LSP) ---
    analyze_xmx: str         # heap разового java analyze (не граф! пофайловый анализ)
    analyze_concurrency: int # очередь: сколько analyze-процессов разом (деф. 1 = последовательно)
    analyze_timeout: float   # таймаут одного analyze-прогона (сек)

    # --- тайминги (сек) ---
    request_timeout: float       # таймаут одиночного LSP-запроса
    index_wait_timeout: float    # макс. ожидание готовности индекса (предохранитель)
    index_settle_sec: float      # затихание $/progress → индекс готов
    index_grace_sec: float       # ожидание начала $/progress (fallback, если молчит)
    diagnostics_wait_sec: float  # ожидание publishDiagnostics по файлу

    @staticmethod
    def from_env() -> "Settings":
        # База для дефолтного пути к jar: в обычном запуске — корень проекта;
        # в PyInstaller-бандле (sys.frozen) — папка рядом с exe (там лежит server/).
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
        else:
            base = Path(__file__).resolve().parents[2]  # .../bsl-ls-mcp
        default_jar = base / "server" / "bsl-language-server-0.29.0-exec.jar"
        cfg = os.environ.get("BSL_CONFIG")
        log = os.environ.get("BSL_SERVER_LOG")
        # Статус-файл в ProgramData — единый путь для службы (LocalSystem) и трея (юзер).
        status = os.environ.get("BSL_STATUS_FILE") or str(
            Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "bsl-ls-mcp" / "status.json")
        return Settings(
            jar_path=Path(os.environ.get("BSL_LS_JAR", str(default_jar))),
            workspace=Path(os.environ.get("BSL_WORKSPACE", r"C:\1c\src\cf")),
            bsl_config=Path(cfg) if cfg else None,
            server_log=Path(log) if log else None,
            status_file=Path(status),
            java_path=os.environ.get("BSL_JAVA", "java"),
            xmx=os.environ.get("BSL_XMX", "14g"),
            mcp_transport=os.environ.get("BSL_MCP_TRANSPORT", "stdio"),
            mcp_host=os.environ.get("BSL_MCP_HOST", "127.0.0.1"),
            mcp_port=int(os.environ.get("BSL_MCP_PORT", "8081")),
            max_concurrency=int(os.environ.get("BSL_MAX_CONCURRENCY", "4")),
            diag_max_chars=int(os.environ.get("BSL_DIAG_MAX_CHARS", "30000")),
            analyze_xmx=os.environ.get("BSL_ANALYZE_XMX", "2g"),
            analyze_concurrency=int(os.environ.get("BSL_ANALYZE_CONCURRENCY", "1")),
            analyze_timeout=float(os.environ.get("BSL_ANALYZE_TIMEOUT", "300")),
            request_timeout=float(os.environ.get("BSL_REQUEST_TIMEOUT", "1200")),
            index_wait_timeout=float(os.environ.get("BSL_INDEX_WAIT_TIMEOUT", "1200")),
            index_settle_sec=float(os.environ.get("BSL_INDEX_SETTLE", "3")),
            index_grace_sec=float(os.environ.get("BSL_INDEX_GRACE", "20")),
            diagnostics_wait_sec=float(os.environ.get("BSL_DIAGNOSTICS_WAIT", "120")),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Единый кэшированный источник настроек (читает ENV один раз).
    Используется везде вместо прямого Settings.from_env() — один согласованный
    снимок конфигурации на процесс."""
    return Settings.from_env()
