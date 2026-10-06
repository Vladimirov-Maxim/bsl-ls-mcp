"""Знание платформы и БСП, которым пользуются проверки, — данными, а не ветвлением.

Всё здесь — умолчания: публичные имена платформы 1С и БСП, ничего проектного.
Проект переопределяет их в `настройки` реестра (см. `Knowledge.with_settings`),
не трогая код инструмента."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

# Функции глобального контекста, которые чаще всего встречаются как имена переменных
# (подставляются в шаблон вместо {ИменаГлобальногоКонтекста}).
GLOBAL_NAMES: tuple[str, ...] = (
    "Строка", "Число", "Дата", "Булево", "Тип", "ТипЗнч", "Формат", "Метаданные", "ТекущаяДата",
    "Лев", "Прав", "Сред", "Найти", "Окр", "Цел", "Мин", "Макс", "Год", "Месяц", "День", "Час",
    "Минута", "Секунда", "Вычислить", "Выполнить",
)

# Вызов, который читает базу: функции БСП чтения реквизитов, выполнение запроса,
# чтение набора записей, получение объекта по ссылке, поиск в менеджере.
DB_READ = (
    r"(?<![\wА-Яа-яЁё])(ОбщегоНазначения\.)?(ЗначениеРеквизитаОбъекта|ЗначенияРеквизитовОбъекта|"
    r"ЗначениеРеквизитаОбъектов|ЗначенияРеквизитовОбъектов)\s*\(|\.Выполнить\s*\(\s*\)|\.ВыполнитьПакет\s*\(|"
    r"\.Прочитать\s*\(\s*\)|\.ПолучитьОбъект\s*\(\s*\)|"
    r"\.(НайтиПоКоду|НайтиПоНаименованию|НайтиПоРеквизиту|ПолучитьПоследнее|ПолучитьПервое)\s*\("
)
DB_WRITE = r"\.Записать\s*\("

# Модальные и немодальные диалоги с пользователем.
DIALOGS: tuple[str, ...] = ("Вопрос", "ПоказатьВопрос", "Предупреждение", "ПоказатьПредупреждение",
                            "ОткрытьФормуМодально")

# Обработчики записи объекта, в которых платформа уже открыла транзакцию.
TRANSACTIONAL_HANDLERS: tuple[str, ...] = ("ПередЗаписью", "ПриЗаписи", "ПередУдалением",
                                           "ОбработкаПроведения", "ОбработкаУдаленияПроведения")
# Обработчики записи, бизнес-логика которых идёт только после проверки ОбменДанными.Загрузка.
EXCHANGE_GUARDED_HANDLERS: tuple[str, ...] = ("ПередЗаписью", "ПриЗаписи", "ПередУдалением")

# Область, методы которой не вызываются из чужих модулей (стандарт структуры модуля).
INTERNAL_API_REGION = "СлужебныйПрограммныйИнтерфейс"

# Литералы, которые выходным параметром не бывают: `Свойство("Имя", Истина)` — установка.
LANGUAGE_LITERALS: tuple[str, ...] = ("Истина", "Ложь", "Неопределено", "Null")

MAX_PARAMS = 5


@dataclass(frozen=True)
class Knowledge:
    prefixes: tuple[str, ...] = ()            # префиксы доработок проекта; пусто — «своих» модулей нет
    global_names: tuple[str, ...] = GLOBAL_NAMES
    db_read: str = DB_READ
    db_write: str = DB_WRITE
    dialogs: tuple[str, ...] = DIALOGS
    transactional_handlers: tuple[str, ...] = TRANSACTIONAL_HANDLERS
    exchange_guarded_handlers: tuple[str, ...] = EXCHANGE_GUARDED_HANDLERS
    internal_api_region: str = INTERNAL_API_REGION
    language_literals: tuple[str, ...] = LANGUAGE_LITERALS
    _compiled: dict = field(default_factory=dict, compare=False, repr=False, init=False)

    # Ключи раздела `настройки` реестра → поле. Всё необязательное.
    SETTINGS_KEYS = {
        "префикс": "prefixes",
        "имена_глобального_контекста": "global_names",
        "чтение_базы": "db_read",
        "запись_базы": "db_write",
        "диалоги": "dialogs",
    }

    def with_settings(self, settings: dict) -> tuple[Knowledge, list[str]]:
        """Применить раздел `настройки` реестра. Возвращает знание и список проблем
        (неверный тип значения) — проблемы не роняют проверку, а называются."""
        changes: dict = {}
        problems: list[str] = []
        for key, attr in self.SETTINGS_KEYS.items():
            if key not in settings:
                continue
            value = settings[key]
            if attr in ("prefixes", "global_names", "dialogs"):
                if isinstance(value, str):
                    value = (value,)
                if not (isinstance(value, (list, tuple)) and all(isinstance(v, str) and v for v in value)):
                    problems.append(f"настройки.{key}: ожидалась строка или список строк")
                    continue
                changes[attr] = tuple(value)
            else:
                if not isinstance(value, str):
                    problems.append(f"настройки.{key}: ожидалась строка-регулярка")
                    continue
                try:
                    re.compile(value)
                except re.error as exc:
                    problems.append(f"настройки.{key}: регулярка не компилируется ({exc})")
                    continue
                changes[attr] = value
        return (replace(self, **changes) if changes else self), problems

    def regex(self, attr: str) -> re.Pattern:
        """Скомпилированная регулярка класса вызовов (без учёта регистра, как в 1С)."""
        if attr not in self._compiled:
            self._compiled[attr] = re.compile(getattr(self, attr), re.IGNORECASE)
        return self._compiled[attr]

    def is_own(self, name: str) -> bool:
        """Имя с префиксом доработок проекта (без учёта регистра)."""
        folded = name.casefold()
        return any(folded.startswith(p.casefold()) for p in self.prefixes)
