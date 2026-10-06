"""Архитектурный гард: зависимости направлены внутрь, каналы разделены, правила
проверки кода не знают ни git, ни диска, ни LSP.

Слои (стрелка — «может импортировать», свой слой — всегда):

    domain          — ни от чего
    application     → domain, settings
    infrastructure  → domain, settings
    composition     → domain, infrastructure, settings      (bootstrap — корень сборки)
    mcp_channel     → application, domain, composition, settings   (application/server.py)
    terminal        → application, domain, composition, settings   (rules_cli.py)
    entry           → всё                                          (cli.py, __main__.py)

MCP и командная строка — два канала одного сценария: друг о друге они не знают.
`mcp` (SDK) живёт только в каналах и точке входа.

Правила проверки кода (`domain/checks`) — чистые функции: никакого диска, процессов,
сети. Старые модули домена, которым путь к файлу нужен по существу (навигация читает
строку кода для ответа), перечислены поимённо — новый модуль в этот список не
попадёт незаметно.

Проверка разбирает исходники через `ast` и ничего не импортирует: гард работает и
тогда, когда пакет сломан."""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "bsl_ls_mcp"
ROOT = PACKAGE.parent

ALLOWED = {
    "domain": set(),
    "application": {"domain", "settings"},
    "infrastructure": {"domain", "settings"},
    "composition": {"domain", "infrastructure", "settings"},
    "settings": set(),
    "mcp_channel": {"application", "domain", "composition", "settings"},
    "terminal": {"application", "domain", "composition", "settings"},
    "entry": {"domain", "application", "infrastructure", "composition", "settings", "mcp_channel",
              "terminal", "entry"},
}
CHANNELS = ("mcp_channel", "terminal")
THIRD_PARTY = {"mcp": ("mcp_channel", "entry")}

CHECKS_FORBIDDEN = ("os", "io", "pathlib", "shutil", "tempfile", "glob", "subprocess", "socket",
                    "asyncio", "urllib", "http")
DOMAIN_FORBIDDEN = ("subprocess", "socket", "shutil", "tempfile", "glob", "asyncio")
# Старые модули домена с путями: типы портов, разбор пути выгрузки, резолв имени в файл
# и чтение строки кода для ответов навигации. Новых сюда не добавлять.
DOMAIN_PATH_LEGACY = {"bsl_ls_mcp.domain.mapper", "bsl_ls_mcp.domain.one_c_config_path",
                      "bsl_ls_mcp.domain.ports", "bsl_ls_mcp.domain.resolver"}


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def layer_of(module: str) -> str | None:
    parts = module.split(".")
    if parts[0] != "bsl_ls_mcp":
        return None
    if len(parts) == 1 or parts[1] in ("cli", "__main__"):
        return "entry"
    if module == "bsl_ls_mcp.application.server":
        return "mcp_channel"
    return {"domain": "domain", "application": "application", "infrastructure": "infrastructure",
            "bootstrap": "composition", "settings": "settings", "rules_cli": "terminal"}.get(parts[1])


def resolve(node: ast.ImportFrom, current: str, is_package: bool) -> str | None:
    if node.level == 0:
        return node.module
    package = current.split(".") if is_package else current.split(".")[:-1]
    base = package[: len(package) - (node.level - 1)]
    if node.module:
        base += node.module.split(".")
    return ".".join(base) or None


def imports_of(path: Path) -> set[str]:
    current = module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    out: set[str] = set()
    for node in ast.walk(tree):          # и вложенные (ленивые) импорты внутри функций
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = resolve(node, current, path.name == "__init__.py")
            if target:
                out.add(target)
                out.update(f"{target}.{a.name}" for a in node.names)
    return out


def sources() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def test_dependencies_point_inward():
    violations = []
    for path in sources():
        current = module_name(path)
        layer = layer_of(current)
        for target in imports_of(path):
            target_layer = layer_of(target)
            if target_layer is None or target_layer == layer:
                continue
            if target_layer not in ALLOWED[layer]:
                violations.append(f"{current} ({layer}) -> {target} ({target_layer})")
    assert not violations, "зависимости наружу:\n" + "\n".join(sorted(set(violations)))


def test_channels_do_not_see_each_other():
    crossed = []
    for path in sources():
        current = module_name(path)
        layer = layer_of(current)
        if layer not in CHANNELS:
            continue
        crossed += [f"{current} -> {t}" for t in imports_of(path)
                    if layer_of(t) in CHANNELS + ("entry",) and layer_of(t) != layer]
    assert not crossed, "каналы видят друг друга:\n" + "\n".join(sorted(set(crossed)))


def test_check_rules_know_no_disk_no_git_no_lsp():
    """Правила проверки: тексты и маски на вход, находки на выход — и ничего больше."""
    violations = []
    for path in sources():
        current = module_name(path)
        if not current.startswith("bsl_ls_mcp.domain.checks"):
            continue
        for target in imports_of(path):
            if target.split(".")[0] in CHECKS_FORBIDDEN:
                violations.append(f"{current} -> {target}")
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                violations.append(f"{current}: open() в строке {node.lineno}")
    assert not violations, "правила проверки трогают внешний мир:\n" + "\n".join(sorted(set(violations)))


def test_domain_runs_no_processes_and_paths_stay_legacy():
    violations = []
    for path in sources():
        current = module_name(path)
        if layer_of(current) != "domain":
            continue
        for target in imports_of(path):
            root = target.split(".")[0]
            if root in DOMAIN_FORBIDDEN:
                violations.append(f"{current} -> {target}")
            if root in ("pathlib", "os", "io") and current not in DOMAIN_PATH_LEGACY \
                    and not current.startswith("bsl_ls_mcp.domain.checks"):
                violations.append(f"{current} -> {target}: путь/диск в новом модуле домена")
    assert not violations, "\n".join(sorted(set(violations)))


def test_third_party_is_locked_in_its_layer():
    leaked = []
    for path in sources():
        current = module_name(path)
        for target in imports_of(path):
            owners = THIRD_PARTY.get(target.split(".")[0])
            if owners is not None and layer_of(current) not in owners:
                leaked.append(f"{current} -> {target}: положено только {owners}")
    assert not leaked, "\n".join(leaked)


def test_every_layer_is_checked():
    """Гард бесполезен, если слой переименовали и он перестал попадать в проверку."""
    seen = {layer_of(module_name(p)) for p in sources()}
    assert seen == set(ALLOWED), f"слои на диске {sorted(seen)} не совпадают с описанными {sorted(ALLOWED)}"
