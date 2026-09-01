# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/);
версии — [SemVer](https://semver.org/lang/ru/).

## [0.1.0] — первый публичный выпуск

### Возможности
- MCP-сервер (FastMCP) над BSL Language Server; транспорты stdio / sse / streamable-http.
- **Проверка кода** — `bsl_diagnostics` через пакетный `analyze` (без индекса):
  адресация модулем корпуса, произвольным путём (`path`) или снипетом кода (`text`);
  фильтр по severity со сводкой, drill-down по коду диагностики.
- **Навигация по тёплому индексу** — `bsl_callers`, `bsl_callees`, `bsl_definition`,
  `bsl_references`; поддержка модулей форм и команд (round-trip адресов).
- **Метрики** — `bsl_complexity` (когнитивная/цикломатическая сложность).
- **Обслуживание** — `bsl_reindex` (полный реиндекс in-place).
- Нативный бандл (PyInstaller + portable JRE), Windows-служба (NSSM) с гашением java
  через Job Object, трей-пультик со статусом индекса.

### Известные ограничения
- Только Windows (служба/скрипты/Job Object).
- Граф вызовов/ссылок — снимок индекса; новые межмодульные связи видны после `bsl_reindex`.
- `bsl_diagnostics` не выявляет «переменная/метод не определён» (нет семантики областей
  видимости — это делает синтаксический контроль конфигуратора/EDT).
- `bsl_complexity` не имеет параметра `path` (код вне индексированного корпуса не покрывает).
