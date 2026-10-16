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


# Версия BSL Language Server, под которую собрана обёртка (jar в server/, build.ps1).
BSL_LS_VERSION = "1.0.7"

# Уровни диагностик, которые обёртка переназначает по умолчанию (BSL_SEVERITY_OVERRIDES).
# UnknownMember BSL LS 1.0.7 — error, но ложных срабатываний много в любом источнике: поля
# выборок запроса, ключи структур, реквизиты регистров из .mdo EDT (замер на bf_edt: все
# проверенные — ложные). На уровне error она ломает гейт «ноль error» и прячет настоящие
# ошибки; на info видна в suppressed и через code="UnknownMember".
DEFAULT_SEVERITY_OVERRIDES = "UnknownMember=info"
_LEVELS = {"error", "warning", "info", "hint"}


def parse_severity_overrides(raw: str) -> tuple[tuple[str, str], ...]:
    """'Код=уровень;Код2=уровень' → ((код, уровень), …). Пусто или '-' — без переназначений."""
    out = []
    for part in (raw or "").replace(",", ";").split(";"):
        part = part.strip()
        if not part or part == "-":
            continue
        code, sep, level = part.partition("=")
        level = level.strip().lower()
        if not sep or not code.strip() or level not in _LEVELS:
            raise ValueError(f"BSL_SEVERITY_OVERRIDES: ожидалось 'Код=error|warning|info|hint', получено {part!r}")
        out.append((code.strip(), level))
    return tuple(out)


@dataclass(frozen=True)
class Settings:
    # --- источник и пути ---
    jar_path: Path           # исполняемый jar BSL LS (pin версии — в server/)
    workspace: Path          # исходники базы 1С (рабочая копия, её индексирует сервер)
    allowed_roots: tuple[Path, ...]  # корни, под которыми разрешён анализ (конфайнмент
                             # path-режима и резолва имён): workspace + BSL_ALLOWED_ROOTS.
                             # Всё вне них отклоняется — защита от обхода каталога/UNC.
    bsl_config: Path | None  # путь к .bsl-language-server.json, необязателен
    server_log: Path | None  # куда писать stderr java-сервера (для диагностики); None — отбрасывать
    status_file: Path        # файл состояния индекса (idle/building/ready) — для мониторинга

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
    index_ready_fallback_sec: float  # ПРЕДОХРАНИТЕЛЬ готовности: если $/progress видели, но
                                 # строгий сигнал (конец тяжёлой фазы с тиками) не пришёл —
                                 # считаем индекс готовым после стольких секунд ПОЛНОЙ тишины
                                 # прогресса. Ловит вырожденный реиндекс (тёплый дисковый кэш
                                 # BSL LS → тяжёлая фаза без report-тиков; потерянный 'end'),
                                 # из-за которого статус вечно висел в 'building'. Заведомо
                                 # длиннее пауз МЕЖДУ фазами (те бывают >20 c).
    diagnostics_wait_sec: float  # ожидание publishDiagnostics по файлу

    # --- проверки кода задачи по правилам проекта (bsl_rules_check) ---
    rules_path: Path | None = None  # реестр правил проекта по умолчанию (BSL_RULES); внешний файл
    git_path: str = "git"           # git для источника правок (BSL_GIT); служба: полный путь надёжнее
    base_branch: str = "develop"    # база правок по умолчанию — merge-base с этой веткой (BSL_BASE_BRANCH);
                                    # пусто или ветки нет — HEAD

    # --- диагностики и свежесть индекса ---
    diag_engine: str = "auto"       # BSL_DIAG_ENGINE: auto — тёплый LSP (контекст конфигурации), пока
                                    # индекс строится — analyze; lsp — всегда LSP (ждать индекс);
                                    # analyze — всегда пакетный analyze (прежнее поведение)
    diag_lsp_max_files: int = 200   # BSL_DIAG_LSP_MAX_FILES: больше файлов в цели — analyze (каталог целиком)
    freshness: bool = True          # BSL_FRESHNESS: сообщать серверу о правках файлов на диске
    fresh_scan_sec: float = 30.0    # BSL_FRESH_SCAN_SEC: обход дерева без git — не чаще раза в N c
    fresh_min_interval: float = 2.0 # BSL_FRESH_MIN_INTERVAL: проверка правок — не чаще раза в N c
    fresh_open_limit: int = 300     # BSL_FRESH_OPEN_LIMIT: сколько изменённых файлов за раз отдавать
                                    # текстом (didOpen/didChange); сверх — didChangeWatchedFiles
    severity_overrides: tuple[tuple[str, str], ...] = (("UnknownMember", "info"),)
                                    # BSL_SEVERITY_OVERRIDES: 'Код=уровень;…' — переназначить уровень
                                    # диагностики (называется в ответе); '-' — без переназначений

    @staticmethod
    def from_env() -> "Settings":
        # База для дефолтного пути к jar: в обычном запуске — корень проекта;
        # в PyInstaller-бандле (sys.frozen) — папка рядом с exe (там лежит server/).
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
        else:
            base = Path(__file__).resolve().parents[2]  # .../bsl-ls-mcp
        default_jar = base / "server" / f"bsl-language-server-{BSL_LS_VERSION}-exec.jar"
        cfg = os.environ.get("BSL_CONFIG")
        log = os.environ.get("BSL_SERVER_LOG")
        # Статус-файл в ProgramData — единый путь для службы (LocalSystem) и трея (юзер).
        status = os.environ.get("BSL_STATUS_FILE") or str(
            Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "bsl-ls-mcp" / "status.json")
        ws = Path(os.environ.get("BSL_WORKSPACE", r"C:\1c\src\cf"))  # выгрузка или <проект EDT>\src
        # Доп. корни для path-режима (внешние обработки и т.п.) — через ; (Windows).
        extra_roots = [Path(p.strip()) for p in os.environ.get("BSL_ALLOWED_ROOTS", "").split(";")
                       if p.strip()]
        return Settings(
            jar_path=Path(os.environ.get("BSL_LS_JAR", str(default_jar))),
            workspace=ws,
            allowed_roots=(ws, *extra_roots),
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
            index_ready_fallback_sec=float(os.environ.get("BSL_INDEX_READY_FALLBACK", "90")),
            diagnostics_wait_sec=float(os.environ.get("BSL_DIAGNOSTICS_WAIT", "120")),
            rules_path=Path(os.environ["BSL_RULES"]) if os.environ.get("BSL_RULES") else None,
            git_path=os.environ.get("BSL_GIT", "git"),
            base_branch=os.environ.get("BSL_BASE_BRANCH", "develop"),
            diag_engine=os.environ.get("BSL_DIAG_ENGINE", "auto").strip().lower() or "auto",
            diag_lsp_max_files=int(os.environ.get("BSL_DIAG_LSP_MAX_FILES", "200")),
            freshness=os.environ.get("BSL_FRESHNESS", "1").strip().lower() not in {"0", "false", "no", "off"},
            fresh_scan_sec=float(os.environ.get("BSL_FRESH_SCAN_SEC", "30")),
            fresh_min_interval=float(os.environ.get("BSL_FRESH_MIN_INTERVAL", "2")),
            fresh_open_limit=int(os.environ.get("BSL_FRESH_OPEN_LIMIT", "300")),
            severity_overrides=parse_severity_overrides(
                os.environ.get("BSL_SEVERITY_OVERRIDES", DEFAULT_SEVERITY_OVERRIDES)),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Единый кэшированный источник настроек (читает ENV один раз).
    Используется везде вместо прямого Settings.from_env() — один согласованный
    снимок конфигурации на процесс."""
    return Settings.from_env()
