"""Канал командной строки для проверок кода задачи — тот же сценарий, что и MCP-метод
`bsl_rules_check`, но без демона: его зовут хуки и CI.

    bsl-ls-mcp rules-check --repo <выгрузка> [--base <коммит>] [--rev <ревизия>] \
        [--rules <реестр.json>] [--only 13.6 13.7] [--paths <путь> ...] [--json] [--out <файл>]
    bsl-ls-mcp rules-check --baseline <эталон> --target <копия> ...
    bsl-ls-mcp task-methods --repo <выгрузка> [--base ...] [--json]
    bsl-ls-mcp diagnostics --repo <выгрузка> [--base ...] [--rev ...] [--rules <реестр.json>] [--json]

diagnostics — диагностики BSL LS (гейт Г1) по модулям задачи: только строки задачи, error →
Fail, warning → Warn, `ид` — код диагностики; исключения — из реестра (`--rules`/BSL_RULES).

Код возврата rules-check и diagnostics: 0 — нарушений нет (предупреждения допустимы), 1 — есть
нарушения уровня Fail, 2 — ошибка входа (репозиторий, база, реестр).
С MCP-каналом этот модуль не знаком: общее у них — только сценарий."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

from .application.diagnostics_check import run_diagnostics_check
from .application.rules_check import InputError, run_rules_check, run_task_methods, source_kind
from .bootstrap import build_analyzer, build_change_source, load_rules
from .domain.checks.registry import ruleset_from_registry
from .domain.ports import SourceError
from .settings import get_settings

COMMANDS = ("rules-check", "task-methods", "diagnostics")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bsl-ls-mcp", description="Проверки кода задачи по правилам проекта.")
    sub = p.add_subparsers(dest="command", required=True)
    for name, help_text in (("rules-check", "правила проекта над правками задачи"),
                            ("task-methods", "новые и изменённые методы задачи"),
                            ("diagnostics", "диагностики BSL LS по строкам задачи (гейт Г1)")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("--repo", help="git-репозиторий выгрузки конфигурации")
        s.add_argument("--base", default=None,
                       help="коммит до начала задачи (по умолчанию — точка ответвления от develop, иначе HEAD)")
        s.add_argument("--rev", help="ревизия вместо рабочей копии")
        s.add_argument("--baseline", help="каталог-эталон (вместе с --target, без git)")
        s.add_argument("--target", help="каталог-копия с правками (вместе с --baseline)")
        s.add_argument("--paths", nargs="*", default=[], help="только эти пути задачи (от корня выгрузки)")
        s.add_argument("--paths-file", help="файл со списком путей задачи, по одному в строке")
        s.add_argument("--json", action="store_true", help="машинный вывод (JSON)")
        s.add_argument("--out", help="записать JSON в файл (UTF-8), а не в консоль")
        if name in ("rules-check", "diagnostics"):
            s.add_argument("--rules", help="реестр правил проекта (JSON); по умолчанию BSL_RULES"
                                           + (" — для diagnostics только исключения" if name == "diagnostics" else ""))
        if name == "diagnostics":
            s.add_argument("--bsl-config", help="конфиг BSL LS (.bsl-language-server.json) — набор диагностик "
                                                "гейта; по умолчанию BSL_CONFIG")
        if name == "rules-check":
            s.add_argument("--only", nargs="*", default=None, help="только эти ид правил")
    return p


def _paths(args: argparse.Namespace) -> list[str] | None:
    paths = list(args.paths or [])
    if args.paths_file:
        text = Path(args.paths_file).read_bytes().decode("utf-8-sig")
        paths += [line.strip() for line in text.splitlines() if line.strip()]
    return paths or None


def _print_findings(result: dict) -> None:
    for f in result["находки"]:
        print(f"[{f['уровень']}] {f['ид']} {f['файл']}:{f['строка']} — {f['текст']}")
    for x in result["исключены"]:
        print(f"[=] {x['ид']} {x['файл']}:{x['строка']} — исключение реестра: {x['основание']}")
    for s in result["пропущено"]:
        print(f"[?] пропущено: {s['что']} — {s['почему']}")
    fails = sum(f["уровень"] == "Fail" for f in result["находки"])
    warns = len(result["находки"]) - fails
    print(f"ИТОГ: нарушений {fails}, предупреждений {warns}, исключено {len(result['исключены'])}, "
          f"пропущено проверок {len(result['пропущено'])}")


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)
    settings = get_settings()
    try:
        source_kind(args.repo, args.baseline, args.target)
        source = build_change_source(settings, repo=args.repo, base=args.base, rev=args.rev,
                                     baseline=args.baseline, target=args.target)
        paths = _paths(args)
        if args.command == "rules-check":
            result = run_rules_check(source, load_rules(settings, args.rules), paths=paths, only=args.only)
        elif args.command == "diagnostics":
            registry = load_rules(settings, args.rules)
            exceptions = list(ruleset_from_registry(registry).exceptions) if registry is not None else []
            if args.bsl_config:
                settings = replace(settings, bsl_config=Path(args.bsl_config))
            result = asyncio.run(run_diagnostics_check(
                source, build_analyzer(settings), paths=paths,
                severity_overrides=settings.severity_overrides, exceptions=exceptions))
        else:
            result = run_task_methods(source, paths=paths)
    except (InputError, SourceError, OSError) as exc:
        print(f"[bsl-ls] {exc}", file=sys.stderr)
        return 2

    if args.json or args.out:
        text = json.dumps(result, ensure_ascii=False, indent=1)
        if args.out:
            Path(args.out).write_bytes(text.encode("utf-8"))
        else:
            print(text)
    elif args.command in ("rules-check", "diagnostics"):
        _print_findings(result)
    else:
        for m in result:
            print(f"{m['статус']:8} {m['модуль']}.{m['метод']} ({m['вид']}"
                  f"{', экспорт' if m['экспорт'] else ''}) {m['файл']}:{m['строки'][0]}-{m['строки'][1]}")

    if args.command in ("rules-check", "diagnostics"):
        return 1 if any(f["уровень"] == "Fail" for f in result["находки"]) else 0
    return 0
