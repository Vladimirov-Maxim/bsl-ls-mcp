"""Находки и ответ в формате договора проверок:
`{"находки": [...], "исключены": [...], "пропущено": [...]}`."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    rule_id: str       # ид правила (у проекта — его номер, без реестра — ключ проверки)
    level: str         # "Fail" | "Warn"
    file: str          # путь от корня выгрузки, прямые косые
    line: int          # номер строки в новой версии файла
    text: str          # текст правила + конкретика (имя метода, вызов)
    regulation: str | None

    def as_dict(self) -> dict:
        return {"ид": self.rule_id, "уровень": self.level, "файл": self.file, "строка": self.line,
                "текст": self.text, "регламент": self.regulation}


@dataclass(frozen=True)
class Excluded:
    finding: Finding
    reason: str

    def as_dict(self) -> dict:
        return {"ид": self.finding.rule_id, "файл": self.finding.file, "строка": self.finding.line,
                "основание": self.reason}


@dataclass(frozen=True)
class Skipped:
    what: str
    why: str

    def as_dict(self) -> dict:
        return {"что": self.what, "почему": self.why}


@dataclass(frozen=True)
class RegistryException:
    """Разрешённое исключение реестра: находка правила `check`, в описании которой
    встречаются и `file`, и `contains`, идёт не в нарушения, а в `исключены`."""
    check: str
    file: str
    contains: str
    reason: str

    def matches(self, f: Finding) -> bool:
        # Описание находки — тот же вид, что в текстовом выводе: «файл:строка — текст».
        # Так совпадает и фрагмент пути, и фрагмент сообщения (имя вызова, метода).
        described = f"{f.file}:{f.line} — {f.text}"
        return f.rule_id == self.check and self.file in described and self.contains in described


def finish(findings: list[Finding], exceptions: list[RegistryException],
           skipped: list[Skipped]) -> dict:
    """Исключения → в `исключены`; порядок — по файлу и строке; дубли (ид+файл+строка)
    схлопываются."""
    kept: list[Finding] = []
    excluded: list[Excluded] = []
    for f in findings:
        rule = next((e for e in exceptions if e.matches(f)), None)
        if rule is None:
            kept.append(f)
        else:
            excluded.append(Excluded(f, rule.reason))
    return {
        "находки": [f.as_dict() for f in _ordered(kept)],
        "исключены": [x.as_dict() for x in _ordered_excluded(excluded)],
        "пропущено": [s.as_dict() for s in _unique_skipped(skipped)],
    }


def _key(f: Finding) -> tuple:
    return (f.file, f.line, _rule_order(f.rule_id))


def _rule_order(rule_id: str) -> tuple:
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in rule_id.split("."))


def _ordered(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in sorted(findings, key=_key):
        k = (f.rule_id, f.file, f.line)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def _ordered_excluded(items: list[Excluded]) -> list[Excluded]:
    seen: set[tuple] = set()
    out: list[Excluded] = []
    for x in sorted(items, key=lambda x: _key(x.finding)):
        k = (x.finding.rule_id, x.finding.file, x.finding.line)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _unique_skipped(items: list[Skipped]) -> list[Skipped]:
    seen: set[tuple] = set()
    out: list[Skipped] = []
    for s in items:
        if (s.what, s.why) not in seen:
            seen.add((s.what, s.why))
            out.append(s)
    return out
