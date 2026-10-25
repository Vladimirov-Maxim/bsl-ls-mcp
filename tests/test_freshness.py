"""Свежесть индекса: правки `.bsl` на диске после начала индексации доходят до сервера
уведомлением. Без java: источник изменений — временный git-репозиторий или
обход дерева; отправку проверяем на клиенте с подменённым транспортом."""
import asyncio
import dataclasses
import os
import time
from pathlib import Path

from checks_support import commit, git, write

from bsl_ls_mcp.infrastructure.freshness import CHANGED, CREATED, DELETED, WorkspaceChanges
from bsl_ls_mcp.infrastructure.stdio_lsp_client import StdioLspClient, _norm_uri
from bsl_ls_mcp.settings import get_settings


def _touch(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    t = time.time() + 5
    os.utime(p, (t, t))                       # mtime точно другой, без sleep


def _kinds(changes, ws):
    return sorted((c.path.relative_to(ws).as_posix(), c.kind) for c in changes)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "develop")
    ws = repo / "App" / "src"
    write(repo, "App/src/Configuration/Configuration.mdo", ["<mdclass:Configuration/>"], eol="\n")
    write(repo, "App/src/CommonModules/А/Module.bsl", ["Процедура П()", "КонецПроцедуры"], eol="\n")
    write(repo, "App/src/CommonModules/Б/Module.bsl", ["Процедура Б()", "КонецПроцедуры"], eol="\n")
    write(repo, "App/src/CommonModules/Грязный/Module.bsl", ["Процедура Г()", "КонецПроцедуры"], eol="\n")
    write(repo, "Вне/Module.bsl", ["Процедура В()", "КонецПроцедуры"], eol="\n")
    commit(repo)
    return repo, ws


def test_git_mode_reports_changes_after_baseline(tmp_path):
    repo, ws = _repo(tmp_path)
    _touch(ws / "CommonModules" / "Грязный" / "Module.bsl", "грязный ДО индексации\n")
    ch = WorkspaceChanges(ws)
    assert ch.mode == "git"
    ch.baseline()                                       # сервер видит «грязный» текст
    assert ch.changes() == []                           # ничего нового — тишина

    _touch(ws / "CommonModules" / "А" / "Module.bsl", "правка\n")
    _touch(ws / "CommonModules" / "Новый" / "Module.bsl", "новый модуль\n")
    (ws / "CommonModules" / "Б" / "Module.bsl").unlink()
    _touch(ws / "CommonModules" / "А" / "А.mdo", "<не bsl/>")      # не .bsl — не наше
    _touch(repo / "Вне" / "Module.bsl", "вне workspace\n")
    assert _kinds(ch.changes(), ws) == [("CommonModules/А/Module.bsl", CHANGED),
                                        ("CommonModules/Б/Module.bsl", DELETED),
                                        ("CommonModules/Новый/Module.bsl", CREATED)]
    assert ch.changes() == []                           # уже сообщено — второй раз не шлём

    git(repo, "checkout", "-q", "--", "App/src/CommonModules/Грязный/Module.bsl")   # вернули к HEAD
    assert _kinds(ch.changes(), ws) == [("CommonModules/Грязный/Module.bsl", CHANGED)]


def test_git_mode_sees_commits_after_baseline(tmp_path):
    repo, ws = _repo(tmp_path)
    ch = WorkspaceChanges(ws)
    ch.baseline()
    _touch(ws / "CommonModules" / "А" / "Module.bsl", "закоммичено после индексации\n")
    commit(repo, "после индексации")                    # рабочая копия чистая, но база ушла
    assert _kinds(ch.changes(), ws) == [("CommonModules/А/Module.bsl", CHANGED)]
    assert ch.changes() == []


def test_scan_mode_without_git(tmp_path):
    ws = tmp_path / "src"
    _touch(ws / "CommonModules" / "А" / "Module.bsl", "а\n")
    _touch(ws / "CommonModules" / "Б" / "Module.bsl", "б\n")
    ch = WorkspaceChanges(ws, scan_interval=0)
    assert ch.mode == "scan"
    ch.baseline()
    assert ch.changes() == []
    _touch(ws / "CommonModules" / "А" / "Module.bsl", "а2\n")
    _touch(ws / "CommonModules" / "В" / "Module.bsl", "в\n")
    (ws / "CommonModules" / "Б" / "Module.bsl").unlink()
    assert _kinds(ch.changes(), ws) == [("CommonModules/А/Module.bsl", CHANGED),
                                        ("CommonModules/Б/Module.bsl", DELETED),
                                        ("CommonModules/В/Module.bsl", CREATED)]


def test_scan_mode_is_throttled(tmp_path):
    ws = tmp_path / "src"
    _touch(ws / "М" / "Module.bsl", "м\n")
    ch = WorkspaceChanges(ws, scan_interval=3600)
    ch.baseline()
    _touch(ws / "М" / "Module.bsl", "м2\n")
    assert ch.changes() == []                           # раньше интервала — не обходим дерево


def _client(ws, **over):
    s = dataclasses.replace(get_settings(), workspace=ws, fresh_min_interval=0, **over)
    c = StdioLspClient(s)
    c._changes = WorkspaceChanges(ws)
    c._changes.baseline()
    sent, synced = [], []

    async def notify(method, params):
        sent.append((method, params))

    async def sync(uri, force=False):
        synced.append(Path(_norm_uri(uri)).parent.name)

    c.notify, c._sync = notify, sync
    return c, sent, synced


def test_client_refresh_syncs_text_of_changed_and_new_files(tmp_path):
    """Изменённые и новые — текстом (обрабатывается по порядку, до следующего запроса);
    удалённые — didClose открытого + didChangeWatchedFiles(Deleted)."""
    repo, ws = _repo(tmp_path)
    c, sent, synced = _client(ws)
    gone = (ws / "CommonModules" / "Грязный" / "Module.bsl").resolve()
    c._open_docs[_norm_uri(gone.as_uri())] = (gone.as_uri(), 1, 0.0)
    _touch(ws / "CommonModules" / "А" / "Module.bsl", "правка\n")
    _touch(ws / "CommonModules" / "Новый" / "Module.bsl", "новый\n")
    gone.unlink()
    assert asyncio.run(c.refresh()) == 3
    assert sorted(synced) == ["А", "Новый"]
    assert [m for m, _ in sent] == ["textDocument/didClose", "workspace/didChangeWatchedFiles"]
    assert [(Path(_norm_uri(x["uri"])).parent.name, x["type"]) for x in sent[1][1]["changes"]] == [
        ("Грязный", DELETED)]
    assert c.is_new((ws / "CommonModules" / "Новый" / "Module.bsl").resolve().as_uri())
    assert not c.is_new((ws / "CommonModules" / "А" / "Module.bsl").resolve().as_uri())


def test_client_refresh_overflow_goes_to_watched(tmp_path):
    repo, ws = _repo(tmp_path)
    c, sent, synced = _client(ws, fresh_open_limit=1)
    _touch(ws / "CommonModules" / "А" / "Module.bsl", "правка\n")
    _touch(ws / "CommonModules" / "Б" / "Module.bsl", "правка\n")
    assert asyncio.run(c.refresh()) == 2
    assert len(synced) == 1
    assert [m for m, _ in sent] == ["workspace/didChangeWatchedFiles"]
    assert [x["type"] for x in sent[0][1]["changes"]] == [CHANGED]


def test_freshness_off_sends_nothing(tmp_path):
    s = dataclasses.replace(get_settings(), workspace=tmp_path, freshness=False)
    c = StdioLspClient(s)
    asyncio.run(c._baseline())
    assert c._changes is None and asyncio.run(c.refresh()) == 0
