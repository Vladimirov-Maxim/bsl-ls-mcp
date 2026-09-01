"""Юнит-тесты чистой логики (без java/LSP): resolver + mapper.
Запуск: PYTHONPATH=src py -3 tests/test_pure.py
Без внешних зависимостей — простые assert + мини-раннер."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bsl_ls_mcp.application import tools                 # noqa: E402
from bsl_ls_mcp.domain import mapper, resolver          # noqa: E402
from bsl_ls_mcp.domain.one_c_naming import type_ru_to_en, type_en_to_ru, full_name  # noqa: E402

WS = ROOT / "tests" / "fixtures" / "smoke_ws"
MOD_URI = (WS / "CommonModules" / "ТестОбщийМодуль" / "Ext" / "Module.bsl").resolve().as_uri()


# ---------- resolver ----------
def test_split_full_name():
    assert resolver.split_full_name("ОбщийМодуль.МойМодуль.Имя") == ("ОбщийМодуль", "МойМодуль", "Имя")
    # символ с точкой не теряется
    assert resolver.split_full_name("ОбщийМодуль.M.А.Б")[2] == "А.Б"
    try:
        resolver.split_full_name("ОбщийМодуль.M")
        assert False, "ожидали ValueError"
    except ValueError:
        pass


def test_candidate_uris():
    # детерминированный список существующих файлов-кандидатов модуля
    assert resolver.candidate_uris(WS, "ОбщийМодуль", "ТестОбщийМодуль") == [MOD_URI]
    assert resolver.candidate_uris(WS, "НетТакогоТипа", "X") == []
    assert resolver.candidate_uris(WS, "ОбщийМодуль", "НетТакого") == []


def test_diagnostics_src_dir():
    # каталог для analyze по имени — детерминированно по раскладке CR-выгрузки.
    cm = (WS / "CommonModules" / "ТестОбщийМодуль" / "Ext").resolve()
    assert resolver.diagnostics_src_dir(WS, "ОбщийМодуль.ТестОбщийМодуль") == cm
    # объект → его Ext (модули объекта, без форм)
    obj = (WS / "Catalogs" / "ТестСправочник" / "Ext").resolve()
    assert resolver.diagnostics_src_dir(WS, "Справочник.ТестСправочник") == obj
    # форма: и каноничное 4-частное, и 5-частное имя → ОДНА папка формы (не ManagerModule)
    form = (WS / "Catalogs" / "ТестСправочник" / "Forms" / "ТестФорма" / "Ext" / "Form").resolve()
    assert resolver.diagnostics_src_dir(WS, "Справочник.ТестСправочник.Форма.ТестФорма") == form
    assert resolver.diagnostics_src_dir(WS, "Справочник.ТестСправочник.Форма.ТестФорма.Форма") == form
    # несуществующее / неизвестный тип → None (чистый отказ, не молча не тот файл)
    assert resolver.diagnostics_src_dir(WS, "Справочник.ТестСправочник.Форма.НетТакой") is None
    assert resolver.diagnostics_src_dir(WS, "НетТипа.X") is None
    assert resolver.diagnostics_src_dir(WS, "ОдноСлово") is None


def test_analyze_label_from_uri():
    u = lambda *p: WS.joinpath(*p).resolve().as_uri()
    L = lambda uri: mapper.analyze_label_from_uri(WS, uri)
    # точная подпись из ПУТИ файла отчёта (разбор пути, не имени) — различает модули
    assert L(u("CommonModules", "ТестОбщийМодуль", "Ext", "Module.bsl")) == "ОбщийМодуль.ТестОбщийМодуль"
    assert L(u("Catalogs", "ТестСправочник", "Ext", "ManagerModule.bsl")) == "Справочник.ТестСправочник.МодульМенеджера"
    assert L(u("Catalogs", "ТестСправочник", "Forms", "ТестФорма", "Ext", "Form", "Module.bsl")) == "Справочник.ТестСправочник.Форма.ТестФорма"
    # путь не под workspace / пусто → None (вызывающий берёт переданное имя)
    assert mapper.analyze_label_from_uri(WS, "file:///C:/somewhere/Foo.bsl") is None
    assert mapper.analyze_label_from_uri(WS, "") is None


def test_diag_label_outside_corpus():
    # подпись file для кода ВНЕ корпуса (внешние обработки): путь относительно srcDir
    from bsl_ls_mcp.application.tools import _diag_label
    src = Path(r"C:\work\task-1\Реализация\Обработка")
    uri = (src / "Ext" / "ObjectModule.bsl").as_uri()
    assert _diag_label(WS, src, {"_src_uri": uri}, "fallback") == "Ext/ObjectModule.bsl"
    assert _diag_label(WS, src, {"_src_uri": ""}, "fallback") == "fallback"  # мусор -> fallback


def test_diagnostics_address_contract():
    # ЗИ-7: ровно один адрес — module_full_name (корпус) ИЛИ path (вне корпуса)
    import asyncio

    from bsl_ls_mcp.application import tools as T
    from bsl_ls_mcp.settings import get_settings

    class FakeAnalyzer:
        async def analyze(self, src_dir):
            return []

    deps = T.Deps(lsp=None, analyzer=FakeAnalyzer(), settings=get_settings())

    def must_raise(**kw):
        try:
            asyncio.run(T.bsl_diagnostics(deps, **kw))
        except T.ResolveError:
            return
        raise AssertionError(f"ожидали ResolveError для {kw}")

    must_raise()                                                # ни одного адреса
    must_raise(module_full_name="ОбщийМодуль.X", path=str(WS))  # оба сразу
    must_raise(path=str(WS / "нет-такого-пути"))                # путь не существует

    # существующий каталог проходит валидацию (анализатор-заглушка -> пусто)
    r = asyncio.run(T.bsl_diagnostics(deps, path=str(WS)))
    assert r == {"diagnostics": [], "suppressed": {"total": 0, "by_code": []}}


def test_diagnostics_address_validation():
    # bsl_diagnostics принимает РОВНО ОДИН адрес: module_full_name | path | text
    import asyncio

    def call(**kw):
        return asyncio.run(tools.bsl_diagnostics(None, **kw))

    for kw in ({}, {"module_full_name": "ОбщийМодуль.X", "path": r"C:\x"},
               {"module_full_name": "ОбщийМодуль.X", "text": "Функция Ф() КонецФункции"},
               {"path": r"C:\x", "text": "..."},
               {"module_full_name": "X", "path": "y", "text": "z"}):
        try:
            call(**kw)
            assert False, f"ожидали ResolveError для {kw}"
        except tools.ResolveError:
            pass


def test_symbol_candidates_forms():
    # символьный резолв: форма/команда/модуль-кинд по маркеру на 3-й позиции
    form = (WS / "Catalogs" / "ТестСправочник" / "Forms" / "ТестФорма" / "Ext" / "Form" / "Module.bsl").resolve().as_uri()
    uris, sym = resolver.symbol_candidates(WS, "Справочник.ТестСправочник.Форма.ТестФорма.МойМетод")
    assert sym == "МойМетод" and form in uris
    # обычный модуль — символ = всё после Тип.Модуль
    uris2, sym2 = resolver.symbol_candidates(WS, "ОбщийМодуль.ТестОбщийМодуль.ВычислитьСумму")
    assert sym2 == "ВычислитьСумму" and len(uris2) == 1
    # маркер модуля-кинда режет символ верно (файла в фикстуре нет — важен разбор имени)
    uris3, sym3 = resolver.symbol_candidates(WS, "Справочник.ТестСправочник.МодульОбъекта.Метод")
    assert sym3 == "Метод"
    # команда — маркер на 3-й позиции
    _, sym4 = resolver.symbol_candidates(WS, "Обработка.X.Команда.Печать.ОбработкаКоманды")
    assert sym4 == "ОбработкаКоманды"


def test_module_file_uri_forms():
    form = (WS / "Catalogs" / "ТестСправочник" / "Forms" / "ТестФорма" / "Ext" / "Form" / "Module.bsl").resolve().as_uri()
    assert resolver.module_file_uri(WS, "Справочник.ТестСправочник.Форма.ТестФорма") == form
    cm = (WS / "CommonModules" / "ТестОбщийМодуль" / "Ext" / "Module.bsl").resolve().as_uri()
    assert resolver.module_file_uri(WS, "ОбщийМодуль.ТестОбщийМодуль") == cm


def test_type_and_module_from_uri_forms():
    # round-trip: uri модуля формы -> адрес с сегментом формы, снова резолвится
    form = (WS / "Catalogs" / "ТестСправочник" / "Forms" / "ТестФорма" / "Ext" / "Form" / "Module.bsl").resolve().as_uri()
    tr, mod = mapper._type_and_module_from_uri(form)
    assert tr == "Справочник" and mod == "ТестСправочник.Форма.ТестФорма"
    uris, _ = resolver.symbol_candidates(WS, f"{tr}.{mod}.Любой")   # инвариант round-trip
    assert form in uris
    # обычный модуль объекта — адрес без хвоста
    mgr = "file:///C:/x/Catalogs/Валюты/Ext/ManagerModule.bsl"
    assert mapper._type_and_module_from_uri(mgr) == ("Справочник", "Валюты")


def test_find_symbol_position():
    symbols = [
        {"name": "ВычислитьСумму", "selectionRange": {"start": {"line": 0, "character": 9}},
         "children": [{"name": "Вложенная", "selectionRange": {"start": {"line": 4, "character": 1}}}]},
        {"name": "Foo(а, б)", "range": {"start": {"line": 9, "character": 10}}},  # форма с сигнатурой
    ]
    assert mapper.find_symbol_position(symbols, "ВычислитьСумму") == (0, 9)
    assert mapper.find_symbol_position(symbols, "Вложенная") == (4, 1)   # рекурсия в children
    assert mapper.find_symbol_position(symbols, "Foo") == (9, 10)        # имя по части до '('
    assert mapper.find_symbol_position(symbols, "Нет") is None


# ---------- one_c_naming ----------
def test_naming_taxonomy():
    assert type_ru_to_en("ОбщийМодуль") == "CommonModule"
    assert type_en_to_ru("Catalog") == "Справочник"
    assert type_ru_to_en("Ерунда") is None
    assert full_name("Справочник", "Валюта") == "Справочник.Валюта"


# ---------- mapper ----------
def test_type_and_module_from_uri():
    assert mapper._type_and_module_from_uri(
        "file:///C:/x/CommonModules/ModX/Ext/Module.bsl") == ("ОбщийМодуль", "ModX")
    assert mapper._type_and_module_from_uri(
        "file:///C:/x/Documents/Реализация/Ext/ObjectModule.bsl")[0] == "Документ"
    assert mapper._type_and_module_from_uri("file:///nowhere.bsl") == ("?", "?")


def test_call_hierarchy_item_to_ref():
    ref = mapper.call_hierarchy_item_to_ref(
        {"uri": "file:///C:/x/CommonModules/ModX/Ext/Module.bsl", "name": "Foo", "kind": 12})
    assert ref.name == "Foo" and ref.type == "ОбщийМодуль"
    assert ref.full_name == "ОбщийМодуль.ModX.Foo" and ref.kind == "function"


def test_lens_helpers():
    assert mapper.lens_complexity_kind({"data": {"id": "CognitiveComplexity"}}) == "cognitive"
    assert mapper.lens_complexity_kind({"data": {"id": "CyclomaticComplexity"}}) == "cyclomatic"
    assert mapper.lens_complexity_kind({"data": {"id": "RunTest"}}) is None
    assert mapper.parse_complexity_number({"command": {"title": "Когнитивная сложность: 5"}}) == 5
    assert mapper.parse_complexity_number({"command": {"title": "нет числа"}}) == 0


def test_diagnostic_and_location():
    d = mapper.diagnostic_to_model("ОбщийМодуль.M",
                                   {"range": {"start": {"line": 4}}, "code": "X",
                                    "severity": 2, "message": "m"})
    assert d.line == 5 and d.severity == "warning" and d.file == "ОбщийМодуль.M"

    loc = mapper.location_to_code_location({"uri": MOD_URI, "range": {"start": {"line": 0}}})
    assert loc.type == "ОбщийМодуль" and loc.module == "ТестОбщийМодуль"
    assert loc.line == 1 and loc.text.startswith("Функция ВычислитьСумму")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
