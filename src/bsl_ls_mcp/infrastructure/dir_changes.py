"""Источник правок — пара каталогов эталон ↔ копия, без git. Это задачи по внешним
обработкам и отчётам: эталон из вложения против правленой копии. Состав — сравнением
деревьев по содержимому (A — только в копии, D — только в эталоне, M — различаются),
строки задачи — `git diff --no-index` двух файлов (тот же алгоритм, что в git-режиме: иначе
одинаковые строки, например пустые, выравниваются по-разному и находка уходит не на
добавленную строку), без git — построчным сравнением двух текстов."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..domain.checks.bsl_line import split_text
from ..domain.checks.mask import mask_from_texts, mask_from_unified_diff, whole_file
from ..domain.ports import Change, SourceError

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _files(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig", errors="replace")


class DirPairChangeSource:
    mode = "каталоги"
    config_prefixes = ("",)

    def __init__(self, baseline: str | Path, target: str | Path, *, git: str | None = "git",
                 timeout: float = 120) -> None:
        self.baseline = Path(baseline).resolve()
        self.target = Path(target).resolve()
        self._git_exe = git
        self._timeout = timeout
        for p in (self.baseline, self.target):
            if not p.is_dir():
                raise SourceError(f"каталог не найден: {p}")
        self._changes: list[Change] | None = None

    def changes(self) -> list[Change]:
        if self._changes is None:
            before, after = _files(self.baseline), _files(self.target)
            out = []
            for rel, p in sorted(after.items()):
                if rel not in before:
                    out.append(Change("A", rel))
                elif p.read_bytes() != before[rel].read_bytes():
                    out.append(Change("M", rel))
            out += [Change("D", rel) for rel in sorted(before) if rel not in after]
            self._changes = out
        return self._changes

    def changed_lines(self, change: Change) -> frozenset[int]:
        new = self.new_text(change.path)
        if new is None:
            return frozenset()
        new_lines = split_text(new)
        old_path = self.baseline / change.path
        if change.status == "A" or not old_path.is_file():
            return whole_file(new_lines)
        mask = self._git_mask(old_path, self.target / change.path)
        if mask is not None:
            return mask
        return mask_from_texts(split_text(_read(old_path)), new_lines)

    def _git_mask(self, old: Path, new: Path) -> frozenset[int] | None:
        """Маска по `git diff --no-index -U0 --ignore-cr-at-eol`; None — git недоступен."""
        if not self._git_exe:
            return None
        cmd = [self._git_exe, "-c", "core.quotepath=off", "diff", "--no-index", "--unified=0", "--no-color",
               "--no-ext-diff", "--no-textconv", "--ignore-cr-at-eol", "--", str(old), str(new)]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=self._timeout, creationflags=_NO_WINDOW,
                               env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode not in (0, 1):      # 1 — файлы различаются
            return None
        return mask_from_unified_diff(r.stdout.decode("utf-8", errors="replace"))

    def old_text(self, change: Change) -> str | None:
        full = self.baseline / change.path
        return _read(full) if full.is_file() else None

    def new_text(self, path: str) -> str | None:
        full = self.target / path
        return _read(full) if full.is_file() else None
