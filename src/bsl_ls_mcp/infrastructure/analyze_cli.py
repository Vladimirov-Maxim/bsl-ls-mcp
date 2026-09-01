"""Проверка кода через пакетный `bsl-language-server analyze` — разовый java-процесс,
БЕЗ LSP и БЕЗ полного индекса (диагностики пофайловы). Отдельный путь от тёплого
графа: навигации индекс нужен, проверке — нет (см. ports.CodeAnalyzer).

Очередь: число одновременных analyze-процессов ограничено семафором
(BSL_ANALYZE_CONCURRENCY, деф. 1 = строго последовательно), чтобы разовые java
(~2 ГБ каждый) не складывались поверх тёплого графа (~12 ГБ) и не съели ОЗУ."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..settings import Settings

_NOWIN = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


class AnalyzeCliRunner:
    """Реализация порта CodeAnalyzer поверх `analyze`-команды jar."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._sem = asyncio.Semaphore(settings.analyze_concurrency)  # очередь

    async def analyze(self, src_dir: Path) -> list[dict[str, Any]]:
        async with self._sem:  # очередь: не плодим java-процессы
            return await asyncio.to_thread(self._run, src_dir)

    def _run(self, src_dir: Path) -> list[dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix="bsl_an_") as out:
            args = [
                self._s.java_path, f"-Xmx{self._s.analyze_xmx}",
                "-jar", str(self._s.jar_path), "analyze",
                "-s", str(src_dir), "-o", out, "-r", "json", "-q",
            ]
            if self._s.bsl_config:
                args += ["-c", str(self._s.bsl_config)]
            subprocess.run(
                args, timeout=self._s.analyze_timeout, creationflags=_NOWIN,
                # cwd = каталог исходников: analyze резолвит путь файла относительно
                # рабочего каталога; если srcDir на ДРУГОМ диске, чем cwd процесса,
                # падает 'other has different root'. cwd=src_dir делает root общим.
                cwd=str(src_dir),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            report = Path(out) / "bsl-json.json"
            if not report.exists():
                return []
            data = json.loads(report.read_text(encoding="utf-8"))
            # прокидываем источник (mdoRef — чистый URI файла) для точной подписи модуля
            return [
                {**d, "_src_uri": fi.get("mdoRef") or fi.get("path", "")}
                for fi in data.get("fileinfos", [])
                for d in fi.get("diagnostics", [])
            ]
