"""Свежесть индекса: какие `.bsl` workspace изменились на диске с начала индексации.

BSL LS узнаёт о правке файла только из уведомления клиента: открытый документ —
`didChange`, остальные — `workspace/didChangeWatchedFiles`. Без уведомления граф вызовов
остаётся на тексте времени индексации, и `bsl_callers` не видит нового вызова из другого
модуля до полного реиндекса.

Источник изменений:
  * git (workspace внутри репозитория) — `git status` + `git diff --name-only <база>`,
    где база — HEAD на момент начала индексации: ловит и правки рабочей копии, и
    коммиты/переключения веток после индексации; ~0.6 c на корпусе;
  * обход дерева по mtime — если репозитория нет; дороже (~3 c на 23 тыс. модулей),
    поэтому не чаще раза в `scan_interval` секунд.

Состояние — «что сервер видел»: путь → mtime (None — файла не было). Изменением считается
расхождение текущего состояния с ним; после выдачи изменения состояние обновляется.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

CREATED, CHANGED, DELETED = 1, 2, 3   # FileChangeType LSP


@dataclass(frozen=True)
class FileChange:
    path: Path
    kind: int          # CREATED | CHANGED | DELETED


def _mtime(p: Path) -> float | None:
    try:
        return p.stat().st_mtime
    except OSError:
        return None


class WorkspaceChanges:
    """Отслеживание правок `.bsl` под workspace между вызовами. Не потокобезопасно —
    вызывающий сериализует (клиент LSP держит свой замок)."""

    def __init__(self, workspace: Path, *, git: str = "git", scan_interval: float = 30.0,
                 timeout: float = 60.0) -> None:
        self.ws = Path(workspace).resolve()
        self._git_exe = git
        self._timeout = timeout
        self._scan_interval = scan_interval
        self._seen: dict[str, float | None] = {}     # ключ — casefold абсолютного пути
        self._paths: dict[str, Path] = {}
        self._base: str | None = None
        self._last_scan = 0.0
        self.repo: Path | None = None          # _git до определения репозитория работает в ws
        self.repo = self._repo_root()
        self.mode = "git" if self.repo else "scan"

    # ---------- git ----------
    def _git(self, *args: str) -> str | None:
        cmd = [self._git_exe, "-C", str(self.repo or self.ws), "-c", "core.quotepath=off",
               "-c", f"safe.directory={(self.repo or self.ws).as_posix()}", "-c", "core.fsmonitor=false", *args]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=self._timeout, creationflags=_NO_WINDOW,
                               env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None

    def _repo_root(self) -> Path | None:
        if not self.ws.is_dir():
            return None
        out = self._git("rev-parse", "--show-toplevel")
        return Path(out.strip()).resolve() if out and out.strip() else None

    def _git_candidates(self) -> dict[Path, bool] | None:
        """Пути, отличающиеся от базы индексации: путь → «новый» (в базе его не было)."""
        rel = self.ws.relative_to(self.repo).as_posix() or "."
        status = self._git("status", "--porcelain=v1", "-z", "-uall", "--no-renames", "--", rel)
        if status is None:
            return None
        out: dict[Path, bool] = {}
        for entry in status.split("\0"):
            if len(entry) >= 4:
                code, path = entry[:2], entry[3:]
                out[self.repo / path] = code in ("??", "A ", "AM")
        if self._base:
            diff = self._git("diff", "--name-status", "-z", "--no-renames", self._base, "--", rel)
            items = (diff or "").split("\0")
            for code, path in zip(items[0::2], items[1::2]):
                if path:
                    out[self.repo / path] = code.startswith("A") or out.get(self.repo / path, False)
        return out

    # ---------- обход дерева ----------
    def _scan(self) -> dict[str, tuple[Path, float]]:
        found: dict[str, tuple[Path, float]] = {}
        for root, _dirs, files in os.walk(self.ws):
            for f in files:
                if f.lower().endswith(".bsl"):
                    p = Path(root) / f
                    m = _mtime(p)
                    if m is not None:
                        found[str(p).casefold()] = (p, m)
        return found

    # ---------- интерфейс ----------
    def baseline(self) -> None:
        """Снимок «что видит сервер» в момент начала индексации."""
        self._seen.clear()
        self._paths.clear()
        if self.mode == "git":
            self._base = (self._git("rev-parse", "HEAD") or "").strip() or None
            for p in self._git_candidates() or {}:
                self._remember(p, _mtime(p))
        else:
            for key, (p, m) in self._scan().items():
                self._seen[key] = m
                self._paths[key] = p
            self._last_scan = time.monotonic()

    def _remember(self, p: Path, m: float | None) -> None:
        key = str(p).casefold()
        self._seen[key] = m
        self._paths[key] = p

    def changes(self) -> list[FileChange]:
        """Изменения с прошлого вызова (или с baseline) — только `.bsl` под workspace."""
        if self.mode == "git":
            cand = self._git_candidates()
            if cand is None:
                return []
            # прежде грязные, а теперь нет: файл вернули к версии из git — это тоже правка
            paths = {str(p).casefold(): (p, new) for p, new in cand.items()}
            for key, p in self._paths.items():
                paths.setdefault(key, (p, False))
            out = []
            for key, (p, new) in paths.items():
                if not key.endswith(".bsl") or not self._under_ws(p):
                    continue
                now = _mtime(p)
                if key in self._seen:
                    was = self._seen[key]
                    if now == was:
                        continue
                    kind = CREATED if was is None else (DELETED if now is None else CHANGED)
                else:
                    # путь впервые отличается от базы: новый, удалённый или изменённый
                    kind = DELETED if now is None else (CREATED if new else CHANGED)
                out.append(FileChange(p, kind))
                self._remember(p, now)
            return out
        if time.monotonic() - self._last_scan < self._scan_interval:
            return []
        current = self._scan()
        self._last_scan = time.monotonic()
        out = []
        for key, (p, m) in current.items():
            was = self._seen.get(key, "absent")
            if was == "absent" or was is None:
                out.append(FileChange(p, CREATED))
            elif was != m:
                out.append(FileChange(p, CHANGED))
        for key, p in list(self._paths.items()):
            if key not in current and self._seen.get(key) is not None:
                out.append(FileChange(p, DELETED))
        self._seen = {k: m for k, (_, m) in current.items()}
        self._paths = {k: p for k, (p, _) in current.items()}
        return out

    def _under_ws(self, p: Path) -> bool:
        try:
            p.resolve().relative_to(self.ws)
            return True
        except (ValueError, OSError):
            return False
