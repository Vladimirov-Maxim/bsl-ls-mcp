"""Источник правок — пара каталогов эталон ↔ копия, без git. Это задачи по внешним
обработкам и отчётам: эталон из вложения против правленой копии. Состав — сравнением
деревьев по содержимому (A — только в копии, D — только в эталоне, M — различаются),
строки задачи — построчным сравнением двух текстов."""
from __future__ import annotations

from pathlib import Path

from ..domain.checks.bsl_line import split_text
from ..domain.checks.mask import mask_from_texts, whole_file
from ..domain.ports import Change, SourceError


def _files(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig", errors="replace")


class DirPairChangeSource:
    mode = "каталоги"

    def __init__(self, baseline: str | Path, target: str | Path) -> None:
        self.baseline = Path(baseline).resolve()
        self.target = Path(target).resolve()
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
        return mask_from_texts(split_text(_read(old_path)), new_lines)

    def new_text(self, path: str) -> str | None:
        full = self.target / path
        return _read(full) if full.is_file() else None
