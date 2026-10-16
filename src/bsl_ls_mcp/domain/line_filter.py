"""Зона правок для диагностик: диапазоны строк и границы методов модуля. Pure."""
from __future__ import annotations

from .checks.bsl_module import ModuleModel


def parse_lines(spec: str) -> tuple[tuple[int, int], ...]:
    """'2660-2781' | '10-20, 35, 40-50' → ((2660, 2781), …); строки 1-based, включительно."""
    out = []
    for part in spec.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        a, sep, b = part.partition("-")
        try:
            lo = int(a)
            hi = int(b) if sep else lo
        except ValueError:
            raise ValueError(f"lines: ожидалось 'от-до' или номер строки, получено {part!r}") from None
        if lo < 1 or hi < lo:
            raise ValueError(f"lines: неверный диапазон {part!r}")
        out.append((lo, hi))
    if not out:
        raise ValueError("lines: пустой список диапазонов")
    return tuple(out)


def method_ranges(lines: list[str], names: list[str]) -> dict[str, tuple[int, int]]:
    """Границы методов по именам (без учёта регистра): имя как в модуле → (начало, конец).
    Начало — первая строка директив и аннотаций над объявлением (&НаСервере, &Перед…):
    диагностики директив относятся к методу; описание-комментарий не включается."""
    wanted = {n.strip().casefold() for n in names if n.strip()}
    out: dict[str, tuple[int, int]] = {}
    for m in ModuleModel(lines).methods:
        if m.name.casefold() in wanted:
            start = m.start
            while start > 1 and lines[start - 2].lstrip().startswith("&"):
                start -= 1
            out[m.name] = (start, m.end)
    return out


def in_ranges(line: int, ranges: tuple[tuple[int, int], ...] | list[tuple[int, int]]) -> bool:
    return any(lo <= line <= hi for lo, hi in ranges)
