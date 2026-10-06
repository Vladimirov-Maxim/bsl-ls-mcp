"""Источник правок — git: база ↔ рабочая копия (вместе с новыми неотслеживаемыми
файлами) или база ↔ ревизия.

Безопасность. Служба работает от LocalSystem, репозиторий принадлежит пользователю:
git откажет («dubious ownership»), поэтому путь репозитория явно помечается
`safe.directory` для этого вызова. Конфиг репозитория умеет запускать команды —
отключаем то, что задевают наши вызовы: внешний diff и textconv (`--no-ext-diff
--no-textconv`), fsmonitor. Сам репозиторий допускается только из разрешённых корней
(проверяет канал)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..domain.checks.bsl_line import split_text
from ..domain.checks.mask import mask_from_unified_diff, whole_file
from ..domain.ports import Change, SourceError

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace")


def detect_unload_format(root: Path) -> None:
    """Выгрузка конфигуратора — поддерживается; EDT — нет (понятная ошибка, а не тишина)."""
    if (root / "Configuration.xml").exists():
        return
    if (root / "Configuration" / "Configuration.mdo").exists() or (root / "src" / "Configuration").exists():
        raise SourceError(f"{root}: формат EDT пока не поддерживается — нужна выгрузка конфигуратора "
                          "(Configuration.xml в корне)")


class GitChangeSource:
    mode = "git"

    def __init__(self, repo: str | Path, base: str = "HEAD", rev: str | None = None, *,
                 git: str = "git", timeout: float = 120) -> None:
        self.repo = Path(repo).resolve()
        if not (self.repo / ".git").exists():
            raise SourceError(f"не git-репозиторий: {self.repo}")
        detect_unload_format(self.repo)
        self.base = base
        self.rev = rev or None
        self._git_exe = git
        self._timeout = timeout
        self._changes: list[Change] | None = None
        for ref in (self.base, self.rev):
            if ref and self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False) is None:
                raise SourceError(f"в репозитории {self.repo} нет ревизии «{ref}»")

    def _git(self, *args: str, check: bool = True) -> str | None:
        cmd = [self._git_exe, "-C", str(self.repo),
               "-c", "core.quotepath=off",
               "-c", f"safe.directory={self.repo.as_posix()}",
               "-c", "core.fsmonitor=false",
               *args]
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C.UTF-8")
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=self._timeout, env=env,
                               creationflags=_NO_WINDOW)
        except FileNotFoundError as exc:
            raise SourceError(f"git не найден ({self._git_exe}): {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SourceError(f"git {args[0]} не ответил за {self._timeout:.0f} c") from exc
        if r.returncode != 0:
            if not check:
                return None
            raise SourceError(f"git {' '.join(args[:2])}: {_decode(r.stderr).strip()}")
        return _decode(r.stdout)

    def _range(self) -> list[str]:
        return [self.base] + ([self.rev] if self.rev else [])

    def changes(self) -> list[Change]:
        if self._changes is None:
            out: list[Change] = []
            listing = self._git("diff", "--name-status", "--no-ext-diff", "--no-textconv", *self._range())
            for line in listing.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                status = parts[0][:1]
                path = parts[2] if status in ("R", "C") and len(parts) > 2 else parts[1]
                out.append(Change(status=status, path=path))
            # Новые файлы задачи ещё не в индексе, а diff видит только отслеживаемые.
            # При сравнении двух ревизий неотслеживаемых нет — всё уже в истории.
            if self.rev is None:
                for path in self._git("ls-files", "--others", "--exclude-standard").splitlines():
                    if path.strip():
                        out.append(Change(status="A", path=path.strip()))
            self._changes = out
        return self._changes

    def changed_lines(self, change: Change) -> frozenset[int]:
        diff = self._git("diff", "--unified=0", "--no-color", "--no-ext-diff", "--no-textconv",
                         *self._range(), "--", change.path)
        mask = mask_from_unified_diff(diff)
        if not mask and change.status == "A" and self.rev is None and "@@" not in diff:
            text = self.new_text(change.path)        # неотслеживаемый файл: git diff его не видит
            if text is not None:
                return whole_file(split_text(text))
        return mask

    def new_text(self, path: str) -> str | None:
        if self.rev is None:
            full = self.repo / path
            if not full.is_file():
                return None
            return _decode(full.read_bytes())
        return self._git("show", f"{self.rev}:{path}", check=False)
