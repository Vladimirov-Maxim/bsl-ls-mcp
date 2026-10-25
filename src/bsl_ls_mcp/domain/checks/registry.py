"""Реестр правил проекта как модель: отбор своих правил, привязка правил «код» к
проверкам каталога, шаблонные правила, исключения, настройки.

Реестр — внешний и чужой: инструмент его не правит и не держит у себя. Разбор
терпимый, но не молчаливый: незнакомые поля и правила других инструментов
пропускаются; всё, что инструмент мог бы выполнить, но не может (нет реализации,
неизвестная область, регулярка не компилируется), называется в `пропущено`.
Без реестра работает встроенный каталог с нейтральными текстами (ид = ключ проверки)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .code_rules import CATALOG, CodeCheck
from .findings import RegistryException, Skipped
from .knowledge import Knowledge
from .templates import AREAS, BUILT_IN_AREAS, TemplateRule, compile_template

TOOL = "bsl-ls"
LEVELS = ("Fail", "Warn")


@dataclass(frozen=True)
class CodeRule:
    rule_id: str
    level: str
    regulation: str | None
    text: str
    check: CodeCheck
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RuleSet:
    templates: tuple[TemplateRule, ...]
    code_rules: tuple[CodeRule, ...]
    exceptions: tuple[RegistryException, ...]
    knowledge: Knowledge
    problems: tuple[Skipped, ...]

    @property
    def rule_ids(self) -> list[str]:
        return [r.rule_id for r in self.templates] + [r.rule_id for r in self.code_rules]

    def only(self, ids: list[str] | None) -> RuleSet:
        """Подмножество правил по ид; несуществующий ид называется в пропусках."""
        if not ids:
            return self
        wanted = set(ids)
        unknown = [i for i in ids if i not in set(self.rule_ids)]
        problems = self.problems + tuple(
            Skipped(f"правило {i}", "нет среди правил инструмента") for i in unknown)
        return RuleSet(
            templates=tuple(r for r in self.templates if r.rule_id in wanted),
            code_rules=tuple(r for r in self.code_rules if r.rule_id in wanted),
            exceptions=self.exceptions, knowledge=self.knowledge, problems=problems)


def default_ruleset() -> RuleSet:
    """Без реестра: все проверки каталога с нейтральными текстами, ид = ключ."""
    rules = tuple(CodeRule(rule_id=c.key, level=c.level, regulation=None, text=c.text, check=c)
                  for c in CATALOG.values())
    return RuleSet(templates=(), code_rules=rules, exceptions=(), knowledge=Knowledge(), problems=())


def ruleset_from_registry(data: object, tool: str = TOOL) -> RuleSet:
    problems: list[Skipped] = []
    if not isinstance(data, dict) or not isinstance(data.get("правила"), list):
        return RuleSet((), (), (), Knowledge(),
                       (Skipped("реестр", "нет списка «правила» — проверки инструмента не выполнялись"),))

    knowledge = Knowledge()
    settings = data.get("настройки")
    if settings is not None:
        if isinstance(settings, dict):
            knowledge, bad = knowledge.with_settings(settings)
            problems += [Skipped("настройки реестра", b) for b in bad]
        else:
            problems.append(Skipped("настройки реестра", "ожидался объект"))

    ours = [r for r in data["правила"] if isinstance(r, dict) and r.get("инструмент") == tool]
    if not ours:
        problems.append(Skipped("реестр", f"нет правил с «инструмент»: «{tool}» — проверки инструмента "
                                          "не выполнялись"))

    templates: list[TemplateRule] = []
    code_rules: list[CodeRule] = []
    seen: set[str] = set()
    for r in ours:
        rule_id = r.get("ид")
        if not isinstance(rule_id, str) or not rule_id:
            problems.append(Skipped("правило без ид", "запись пропущена"))
            continue
        what = f"правило {rule_id}"
        if rule_id in seen:
            problems.append(Skipped(what, "повтор ид — выполняется первая запись"))
            continue
        seen.add(rule_id)
        level = r.get("уровень")
        if level not in LEVELS:
            problems.append(Skipped(what, f"уровень «{level}» — ожидался Fail или Warn"))
            continue
        text = r.get("текст") if isinstance(r.get("текст"), str) else ""
        regulation = r.get("регламент")
        regulation = None if regulation is None else str(regulation)
        kind = r.get("вид")
        if kind == "шаблон":
            rule = _template(r, rule_id, level, regulation, text, knowledge, problems)
            if rule is not None:
                templates.append(rule)
        elif kind == "код":
            key = r.get("реализация")
            if not isinstance(key, str) or not key:
                problems.append(Skipped(what, "нет поля «реализация» — не указана проверка инструмента"))
                continue
            check = CATALOG.get(key)
            if check is None:
                problems.append(Skipped(what, f"неизвестная реализация «{key}» в этой версии инструмента"))
                continue
            params = r.get("параметры") if isinstance(r.get("параметры"), dict) else {}
            code_rules.append(CodeRule(rule_id, level, regulation, text or check.text, check, params))
        else:
            problems.append(Skipped(what, f"неизвестный вид «{kind}»"))

    own_ids = {t.rule_id for t in templates} | {c.rule_id for c in code_rules}
    exceptions: list[RegistryException] = []
    for e in data.get("исключения") or []:
        if not isinstance(e, dict):
            continue
        fields = [e.get(k) for k in ("проверка", "файл", "содержит", "основание")]
        if fields[0] not in own_ids:
            continue      # исключение к чужому правилу применяет его инструмент
        if not all(isinstance(v, str) for v in fields):
            problems.append(Skipped(f"исключение к {fields[0]}", "ожидались строки проверка/файл/содержит/основание"))
            continue
        exceptions.append(RegistryException(*fields))

    return RuleSet(tuple(templates), tuple(code_rules), tuple(exceptions), knowledge, tuple(problems))


def _template(r: dict, rule_id: str, level: str, regulation: str | None, text: str,
              knowledge: Knowledge, problems: list[Skipped]) -> TemplateRule | None:
    what = f"правило {rule_id}"
    area = r.get("где")
    if area not in AREAS:
        problems.append(Skipped(what, f"неизвестная область «{area}» в этой версии инструмента"))
        return None
    pattern = None
    if area not in BUILT_IN_AREAS:
        source = r.get("шаблон")
        if not isinstance(source, str) or not source:
            problems.append(Skipped(what, "нет шаблона"))
            return None
        try:
            pattern = compile_template(source, knowledge.global_names)
        except re.error as exc:
            problems.append(Skipped(what, f"шаблон не компилируется: {exc}"))
            return None
    files = None
    if r.get("файлы"):
        try:
            files = re.compile(str(r["файлы"]), re.IGNORECASE)
        except re.error as exc:
            problems.append(Skipped(what, f"фильтр «файлы» не компилируется: {exc}"))
            return None
    hit = tuple(x for x in (r.get("ловит") or []) if isinstance(x, str))
    miss = tuple(x for x in (r.get("не_ловит") or []) if isinstance(x, str))
    return TemplateRule(rule_id, level, regulation, text, area, pattern, files, hit, miss)


def example_mismatches(rule: TemplateRule) -> list[str]:
    """Примеры шаблонного правила, на которых оно ведёт себя не так, как обещано."""
    bad = [f"не ловит пример «{x}»" for x in rule.hit_examples if not rule.example_hits(x)]
    bad += [f"ловит правильный пример «{x}»" for x in rule.miss_examples if rule.example_hits(x)]
    if not rule.hit_examples or not rule.miss_examples:
        bad.append("нет примеров «ловит» и/или «не_ловит»")
    return bad
