"""Реализация порта LspServer: запускает BSL LS как дочерний процесс в режиме
stdio-LSP и говорит с ним по JSON-RPC (Content-Length framing — без лимита 8 КБ,
в отличие от websocket; см. spike/RESULTS.md §4).

Живучесть: при падении сервера сессия восстанавливается (`_ensure_started`),
зависшие запросы освобождаются, ошибки reader-таска не глотаются."""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.parse
from typing import Any

from ..domain.mapper import uri_to_path  # единый uri→path (без дубля)
from ..settings import Settings
from .winjob import assign_kill_on_close


def _norm_uri(uri: str) -> str:
    """Канонический ключ URI: BSL LS публикует диагностики с декодированной
    кириллицей, а pathlib.as_uri() даёт percent-encoded. Сводим к одному виду."""
    return urllib.parse.unquote(uri)


class StdioLspClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._ready_task: asyncio.Task | None = None
        self._log_fh = None
        self._start_lock = asyncio.Lock()
        self._doc_lock = asyncio.Lock()   # сериализует _sync (атомарность open/change)
        self._stopping = False
        # Поколение сессии: растёт на каждый _spawn. Reader умирающей java НЕ должен
        # рушить состояние уже поднятой новой сессии (гонка при restart/реиндексе:
        # его finally затирал status в idle, сбрасывал _ready и рвал _pending новой).
        self._gen = 0
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        # открытые документы (uri, version, mtime) — для синхронизации свежести
        self._open_docs: dict[str, tuple[str, int, float]] = {}
        self._diag_waiters: dict[str, list[asyncio.Future]] = {}
        # готовность индекса
        self._ready = asyncio.Event()
        self._progress_seen = False
        self._last_progress = 0.0
        self._progress_active: set[str] = set()
        self._progress_ticks: dict[str, int] = {}
        self._heavy_done = False

    # ---------- жизненный цикл ----------
    async def start(self) -> None:
        await self._ensure_started()

    async def _ensure_started(self) -> None:
        """Гарантирует живой сервер: если процесс не запущен/упал — (пере)поднимает."""
        if self._proc is not None and self._proc.returncode is None:
            return
        async with self._start_lock:
            if self._proc is not None and self._proc.returncode is None:
                return
            await self._spawn()

    async def _spawn(self) -> None:
        # Сразу инвалидируем reader прошлой сессии: с этого момента его finally
        # не тронет ни status, ни _ready, ни _pending новой (см. _read_loop).
        self._gen += 1
        self._reset_state()
        stderr = asyncio.subprocess.DEVNULL
        if self._s.server_log:
            self._log_fh = open(self._s.server_log, "ab")  # noqa: SIM115
            stderr = self._log_fh
        args = ["-Xmx" + self._s.xmx, "-jar", str(self._s.jar_path), "lsp"]
        if self._s.bsl_config:
            args += ["-c", str(self._s.bsl_config)]
        self._proc = await asyncio.create_subprocess_exec(
            self._s.java_path, *args,        # путь к java настраивается (portable JRE)
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
        )
        # java умрёт вместе с этим процессом при любом завершении (в т.ч. taskkill /F)
        assign_kill_on_close(self._proc.pid)
        self._reader_task = asyncio.create_task(self._read_loop(self._gen))

        root_uri = self._s.workspace.resolve().as_uri()
        await self.request("initialize", {
            "processId": None,
            "rootUri": root_uri,
            "capabilities": {
                "window": {"workDoneProgress": True},  # → сервер шлёт $/progress индексации
                "textDocument": {
                    "callHierarchy": {"dynamicRegistration": True},
                    "publishDiagnostics": {"relatedInformation": True},
                    "synchronization": {"dynamicRegistration": True},
                },
                "workspace": {"workspaceFolders": True, "configuration": True},
            },
            "workspaceFolders": [{"uri": root_uri, "name": "ws"}],
        })
        await self.notify("initialized", {})
        self._write_status("building")   # индексация пошла → трей покажет «индексирую»
        # Индексация идёт асинхронно (на корпусе ~1.5 мин). start() НЕ блокируется —
        # готовность выставит watcher; методы порта ждут её (wait_ready).
        self._ready_task = asyncio.create_task(self._watch_ready())

    def _reset_state(self) -> None:
        self._fail_pending(ConnectionError("LSP-сессия перезапускается"))
        self._open_docs.clear()
        self._ready.clear()
        self._progress_seen = False
        self._last_progress = 0.0
        self._progress_active.clear()
        self._progress_ticks.clear()
        self._heavy_done = False

    def _fail_pending(self, exc: Exception) -> None:
        """Освободить зависшие запросы/ожидания (при рестарте/обрыве)."""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
        for waiters in self._diag_waiters.values():
            for fut in waiters:
                if not fut.done():
                    fut.set_exception(exc)
        self._diag_waiters.clear()

    def _write_status(self, state: str) -> None:
        """Состояние индекса в файл для трея: idle | building | ready. Best-effort.

        Пишет ТОЛЬКО клиент, который реально поднимал java (_gen > 0). Клиент, ничего
        не запускавший, статус-файл не трогает: иначе любой CLI-процесс (--reindex),
        у которого при импорте создаются _deps + atexit -> kill(), при выходе затирал
        status работающего демона в idle (трей показывал «индекса нет» во время сборки).
        """
        if self._gen == 0:
            return
        try:
            f = self._s.status_file
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"index": state}), encoding="utf-8")
        except Exception:
            pass

    async def stop(self) -> None:
        self._stopping = True
        self._write_status("idle")
        try:
            if self._proc and self._proc.returncode is None:
                await self.request("shutdown", {}, timeout=10)
                await self.notify("exit", {})
                await asyncio.wait_for(self._proc.wait(), timeout=10)
        except Exception:
            if self._proc:
                self._proc.kill()
        finally:
            for task in (self._ready_task, self._reader_task):
                if task:
                    task.cancel()
            if self._log_fh:
                self._log_fh.close()
                self._log_fh = None

    def kill(self) -> None:
        """Синхронно прибить java (для atexit при остановке демона)."""
        self._stopping = True
        self._write_status("idle")
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.kill()
            except Exception:
                pass

    async def restart(self) -> None:
        """Полный реиндекс in-place: погасить текущую java и поднять свежую (индекс
        с нуля), НЕ роняя демон/соединения. _spawn сбрасывает состояние и запускает
        индексацию; готовность нового индекса дождётся следующий вызов инструмента."""
        async with self._start_lock:
            proc = self._proc
            if proc and proc.returncode is None:
                try:
                    await self.request("shutdown", {}, timeout=10)
                    await self.notify("exit", {})
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except Exception:
                    proc.kill()
            await self._spawn()

    # ---------- готовность индекса ----------
    def _on_progress(self, params: dict) -> None:
        """Учёт фаз индексации BSL LS. populateContext шлёт 3 фазы begin/end;
        ТЯЖЁЛАЯ (наполнение контекста) шлёт report-тики по файлам. Индекс готов,
        когда тикающая фаза закончилась и все фазы закрыты (см. _watch_ready).
        Ловить «по тишине» нельзя — пауза МЕЖДУ фазами на корпусе срабатывает рано."""
        token = str(params.get("token"))
        kind = (params.get("value") or {}).get("kind")
        self._progress_seen = True
        self._last_progress = asyncio.get_running_loop().time()
        if kind == "begin":
            self._progress_active.add(token)
            self._progress_ticks[token] = 0
        elif kind == "report":
            self._progress_ticks[token] = self._progress_ticks.get(token, 0) + 1
        elif kind == "end":
            self._progress_active.discard(token)
            if self._progress_ticks.pop(token, 0) >= 1:
                self._heavy_done = True  # тикающая фаза (наполнение) завершилась

    async def _watch_ready(self) -> None:
        """Готовность индекса = конец ТИКАЮЩЕЙ фазы (наполнение контекста) + закрытие
        всех фаз + короткое затишье. Это единственный надёжный сигнал: «тишина» или
        затишье-по-grace ложно срабатывают в паузах МЕЖДУ фазами на корпусе (бывают
        >20 c) — проверено, не использовать.

        Грубый workspace без .bsl (тяжёлой фазы нет) — это мисконфиг: первый вызов
        упрётся в ResolveError раньше, а gate в худшем случае честно отвалится по
        index_wait_timeout. Намеренно НЕ ставим таймерный fallback, чтобы не вернуть
        преждевременное срабатывание."""
        settle = self._s.index_settle_sec
        grace = self._s.index_grace_sec
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        while not self._progress_seen and loop.time() - t0 < grace:
            await asyncio.sleep(0.5)
        if not self._progress_seen:
            self._ready.set()  # сервер вообще не шлёт $/progress (нет capability)
            self._write_status("ready")
            return
        while not (
            self._heavy_done
            and not self._progress_active
            and loop.time() - self._last_progress >= settle
        ):
            await asyncio.sleep(0.5)
        self._ready.set()
        self._write_status("ready")

    async def wait_ready(self) -> None:
        """Дождаться готовности индекса (первый вызов на холодном сервере)."""
        await asyncio.wait_for(self._ready.wait(), self._s.index_wait_timeout)

    # ---------- транспорт (Content-Length framing) ----------
    async def _read_loop(self, gen: int) -> None:
        proc = self._proc
        assert proc and proc.stdout
        out = proc.stdout
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = await out.readline()
                    if not line:
                        return  # процесс закрыл stdout (штатно или упал)
                    line = line.decode("ascii").strip()
                    if line == "":
                        break
                    k, _, v = line.partition(":")
                    headers[k.strip().lower()] = v.strip()
                length = int(headers.get("content-length", 0))
                if length <= 0:
                    continue
                body = await out.readexactly(length)
                self._dispatch(json.loads(body.decode("utf-8")))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # не глотаем: иначе сессия молча зависнет
            if not self._stopping and gen == self._gen:
                print(f"[bsl-ls] reader error: {exc!r}", file=sys.stderr)
        finally:
            # Обрыв связи: освобождаем зависшие запросы и сбрасываем готовность,
            # чтобы следующий вызов пересоздал сессию (_ensure_started), а не висел.
            # ТОЛЬКО для СВОЕГО поколения: при restart старая java умирает уже после
            # того, как поднялась новая, и её finally иначе затирал status в idle,
            # сбрасывал _ready и рвал _pending новой сессии (реиндекс «залипал»).
            if not self._stopping and gen == self._gen:
                self._fail_pending(ConnectionError("LSP-соединение закрыто"))
                self._ready.clear()
                self._write_status("idle")   # java упала → не готов

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                fut.set_result(msg)
        elif msg.get("method") == "$/progress":
            self._on_progress(msg.get("params", {}))
        elif msg.get("method") == "textDocument/publishDiagnostics":
            p = msg["params"]
            key = _norm_uri(p["uri"])
            diags = p["diagnostics"]
            for fut in self._diag_waiters.pop(key, []):
                if not fut.done():
                    fut.set_result(diags)
        elif "id" in msg and "method" in msg:
            # запрос сервер->клиент (workDoneProgress/create, configuration) — безопасный дефолт
            result: Any = None
            if msg["method"] == "workspace/configuration":
                result = [None] * len(msg.get("params", {}).get("items", []))
            asyncio.create_task(self._reply(msg["id"], result))
        # прочие нотификации (logMessage и т.п.) — игнор

    async def _reply(self, msg_id: Any, result: Any) -> None:
        """Ответ на запрос сервер->клиент; не роняем при обрыве сокета."""
        try:
            await self._send({"jsonrpc": "2.0", "id": msg_id, "result": result})
        except Exception:
            pass

    async def _send(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        data = json.dumps(obj).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + data)
        await self._proc.stdin.drain()

    async def request(self, method: str, params: dict, timeout: float | None = None) -> Any:
        self._id += 1
        i = self._id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[i] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
            msg = await asyncio.wait_for(fut, timeout if timeout is not None else self._s.request_timeout)
        finally:
            self._pending.pop(i, None)  # не копим зависшие futures
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result")

    async def notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ---------- синхронизация свежести ----------
    async def _sync(self, uri: str, force: bool = False) -> None:
        """Привести содержимое документа на сервере к диску: первый раз didOpen,
        далее didChange — если файл изменился (mtime новее) ИЛИ force (диагностикам
        нужен свежий publish даже без правок). Так навигация/сложность видят правки
        модулей, не переиндексируя всё; лишних didChange по неизменным файлам нет.

        Файловый I/O — через to_thread (не блокирует event loop на крупных модулях);
        _doc_lock делает проверку-и-открытие атомарной (нет двойного didOpen)."""
        key = _norm_uri(uri)
        path = uri_to_path(uri)
        async with self._doc_lock:
            try:
                mtime = (await asyncio.to_thread(path.stat)).st_mtime
            except OSError:
                return  # файла нет — синхронизировать нечего
            entry = self._open_docs.get(key)
            if entry is not None and not force and mtime <= entry[2]:
                return  # сервер уже видит текущую версию
            try:
                text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            except OSError:
                return
            if entry is None:
                self._open_docs[key] = (uri, 1, mtime)
                await self.notify("textDocument/didOpen", {
                    "textDocument": {"uri": uri, "languageId": "bsl", "version": 1, "text": text},
                })
            else:
                ver = entry[1] + 1
                self._open_docs[key] = (entry[0], ver, mtime)
                await self.notify("textDocument/didChange", {
                    "textDocument": {"uri": entry[0], "version": ver},
                    "contentChanges": [{"text": text}],
                })

    # ---------- методы порта ----------
    async def diagnostics(self, uri: str) -> list[dict]:
        await self._ensure_started()
        await self.wait_ready()
        key = _norm_uri(uri)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._diag_waiters.setdefault(key, []).append(fut)
        await self._sync(uri, force=True)  # форсим свежий publishDiagnostics
        try:
            return await asyncio.wait_for(fut, self._s.diagnostics_wait_sec)
        except asyncio.TimeoutError:
            return []
        finally:
            waiters = self._diag_waiters.get(key)
            if waiters and fut in waiters:
                waiters.remove(fut)

    async def prepare_call_hierarchy(self, uri: str, line: int, character: int) -> list[dict]:
        await self._ensure_started()
        await self.wait_ready()
        return await self.request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": uri}, "position": {"line": line, "character": character},
        }) or []

    async def incoming_calls(self, item: dict) -> list[dict]:
        return await self.request("callHierarchy/incomingCalls", {"item": item}) or []

    async def outgoing_calls(self, item: dict) -> list[dict]:
        return await self.request("callHierarchy/outgoingCalls", {"item": item}) or []

    async def definition(self, uri: str, line: int, character: int) -> list[dict]:
        await self._ensure_started()
        await self.wait_ready()
        res = await self.request("textDocument/definition", {
            "textDocument": {"uri": uri}, "position": {"line": line, "character": character},
        })
        return res if isinstance(res, list) else ([res] if res else [])

    async def references(self, uri: str, line: int, character: int) -> list[dict]:
        await self._ensure_started()
        await self.wait_ready()
        return await self.request("textDocument/references", {
            "textDocument": {"uri": uri}, "position": {"line": line, "character": character},
            "context": {"includeDeclaration": False},
        }) or []

    async def document_symbol(self, uri: str) -> list[dict]:
        await self._ensure_started()
        await self.wait_ready()
        await self._sync(uri)  # свежесть: позиции из актуального текста файла
        return await self.request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        }) or []

    async def code_lens(self, uri: str) -> list[dict]:
        await self._ensure_started()
        await self.wait_ready()
        await self._sync(uri)  # свежесть: сложность по актуальному тексту
        return await self.request("textDocument/codeLens", {
            "textDocument": {"uri": uri},
        }) or []

    async def resolve_code_lens(self, lens: dict) -> dict:
        return await self.request("codeLens/resolve", lens) or {}
