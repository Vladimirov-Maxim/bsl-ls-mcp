"""Текстовый поиск по `.bsl` workspace: файлы, где встречается фрагмент (кандидаты для
разбора вызовов по строковому имени). Внутри git-репозитория — `git grep -F` (~0.6 c на
корпусе), иначе обход дерева. Регистр фрагмента учитывается: точное сравнение делает
вызывающий; `git grep -i` с кириллицей в этой среде не работает."""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _read(p: Path) -> str | None:
    try:
        return p.read_bytes().decode("utf-8-sig", errors="replace")
    except OSError:
        return None


class WorkspaceTextSearch:
    def __init__(self, workspace: Path, *, git: str = "git", timeout: float = 120.0) -> None:
        self.ws = Path(workspace).resolve()
        self._git_exe = git
        self._timeout = timeout
        self._repo: Path | None | bool = False      # False — ещё не определяли

    def _run_git(self, cwd: Path, *args: str) -> str | None:
        cmd = [self._git_exe, "-C", str(cwd), "-c", "core.quotepath=off",
               "-c", f"safe.directory={cwd.as_posix()}", "-c", "core.fsmonitor=false", *args]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=self._timeout, creationflags=_NO_WINDOW,
                               env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        except (OSError, subprocess.TimeoutExpired):
            return None
        # git grep: 1 — «не найдено», это не ошибка
        if r.returncode not in (0, 1):
            return None
        return r.stdout.decode("utf-8", errors="replace")

    def _repo_root(self) -> Path | None:
        if self._repo is False:
            out = self._run_git(self.ws, "rev-parse", "--show-toplevel") if self.ws.is_dir() else None
            self._repo = Path(out.strip()).resolve() if out and out.strip() else None
        return self._repo  # type: ignore[return-value]

    def files_with(self, fragment: str) -> list[tuple[Path, str]]:
        """(путь, текст) файлов `.bsl` под workspace, содержащих fragment."""
        repo = self._repo_root()
        if repo is not None:
            rel = self.ws.relative_to(repo).as_posix()
            spec = f"{rel}/*.bsl" if rel not in ("", ".") else "*.bsl"
            # Отслеживаемые — git grep по рабочей копии (~0.6 c); неотслеживаемые (новые модули
            # задачи) — списком и вручную: `git grep --untracked` на корпусе стоит ~5 c.
            # неотслеживаемые — из git status по каталогу (~0.6 c; с шаблоном *.bsl и через
            # `ls-files --others` — 1–2 c); обе команды параллельно
            with ThreadPoolExecutor(max_workers=2) as pool:
                grep = pool.submit(self._run_git, repo, "grep", "-l", "-z", "-F", "-e", fragment, "--", spec)
                st = pool.submit(self._run_git, repo, "status", "--porcelain=v1", "-z", "-uall", "--", rel or ".")
                out, status = grep.result(), st.result()
            if out is not None and status is not None:
                found = [(p, t) for p in (repo / x for x in out.split("\0") if x) if (t := _read(p)) is not None]
                for entry in status.split("\0"):
                    if (entry.startswith("?? ") and entry.lower().endswith(".bsl")
                            and (t := _read(repo / entry[3:])) is not None and fragment in t):
                        found.append((repo / entry[3:], t))
                return found
        found = []
        for root, _dirs, files in os.walk(self.ws):
            for f in files:
                if f.lower().endswith(".bsl"):
                    p = Path(root) / f
                    t = _read(p)
                    if t is not None and fragment in t:
                        found.append((p, t))
        return found
