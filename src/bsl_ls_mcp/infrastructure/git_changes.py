"""Источник правок — git: база ↔ рабочая копия (вместе с новыми неотслеживаемыми
файлами) или база ↔ ревизия.

Репозиторий — выгрузка конфигуратора (Configuration.xml в корне) или проект EDT, где
конфигурация лежит глубже (`BF/src/Configuration/Configuration.mdo`): пути правок
остаются от корня репозитория, а префиксы корней конфигурации даются проверкам, чтобы
найти соседний общий модуль.

База по умолчанию — точка ответвления от ветки разработки (`develop`, BSL_BASE_BRANCH):
задача живёт в своей ветке, и её правки — всё, что сделано после ответвления, а не
после последнего коммита. Нет такой ветки — `HEAD`, как раньше.

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


# Глубже не ищем: в проекте EDT корень конфигурации — `<Проект>/src/`.
_CONFIG_SEARCH_DEPTH = 3


def config_prefixes(root: Path) -> tuple[str, ...]:
    """Префиксы корней конфигурации от корня репозитория: '' для выгрузки в корне,
    'BF/src/' для проекта EDT. Не найдено ни одного — ('',), как раньше."""
    found: list[str] = []

    def walk(d: Path, rel: str, depth: int) -> None:
        if (d / "Configuration.xml").is_file() or (d / "Configuration" / "Configuration.mdo").is_file():
            found.append(rel)
            return
        if depth >= _CONFIG_SEARCH_DEPTH:
            return
        try:
            subdirs = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return
        for sub in subdirs:
            walk(sub, f"{rel}{sub.name}/", depth + 1)

    walk(root, "", 0)
    return tuple(found) or ("",)


class GitChangeSource:
    mode = "git"

    def __init__(self, repo: str | Path, base: str | None = None, rev: str | None = None, *,
                 git: str = "git", timeout: float = 120, base_branch: str = "develop") -> None:
        self.repo = Path(repo).resolve()
        if not (self.repo / ".git").exists():
            raise SourceError(f"не git-репозиторий: {self.repo}")
        self.config_prefixes = config_prefixes(self.repo)
        self.rev = rev or None
        self._git_exe = git
        self._timeout = timeout
        self._changes: list[Change] | None = None
        if self.rev and not self._has_commit(self.rev):
            raise SourceError(f"в репозитории {self.repo} нет ревизии «{self.rev}»")
        if base:
            if not self._has_commit(base):
                raise SourceError(f"в репозитории {self.repo} нет ревизии «{base}»")
            self.base, how = base, "задана"
        else:
            self.base, how = self._auto_base(base_branch)
        self.base_info = {"ревизия": self.base, "как": how}

    def _has_commit(self, ref: str) -> bool:
        return self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False) is not None

    def _auto_base(self, branch: str) -> tuple[str, str]:
        """Точка ответвления от `branch` (локальной или origin/), иначе HEAD."""
        for ref in (branch, f"origin/{branch}") if branch else ():
            if not self._has_commit(ref):
                continue
            mb = self._git("merge-base", ref, self.rev or "HEAD", check=False)
            if mb and mb.strip():
                return mb.strip(), f"merge-base с {ref}"
        return "HEAD", "HEAD (ветки разработки нет)"

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
