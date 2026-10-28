"""Команда `diagnostics`: диагностики BSL LS по строкам задачи в формате договора проверок.
Мини-проект: модули задачи + описания их объектов и форм (EDT — с DT-INF и src/), тексты — из
источника правок; находки — только строки задачи, error → Fail, warning → Warn."""
import asyncio
import subprocess
import sys
from pathlib import Path

from checks_support import ROOT, commit, git, new_repo, write

from bsl_ls_mcp.application.diagnostics_check import run_diagnostics_check
from bsl_ls_mcp.domain import task_project as tp
from bsl_ls_mcp.domain.checks.findings import RegistryException
from bsl_ls_mcp.infrastructure.dir_changes import DirPairChangeSource
from bsl_ls_mcp.infrastructure.git_changes import GitChangeSource

EDT_FORM = "App/src/Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl"


def test_descriptions_and_paths():
    edt, des = tp.Project("App/src/", tp.EDT), tp.Project("", tp.DESIGNER)
    assert tp.split_prefix(EDT_FORM, ("", "App/src/")) == ("App/src/", "Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl")
    assert tp.configuration_paths("App/src/") == {tp.EDT: "App/src/Configuration/Configuration.mdo",
                                                 tp.DESIGNER: "App/src/Configuration.xml", "pmf": "App/DT-INF/PROJECT.PMF"}
    assert tp.descriptions("Catalogs/Спр/Forms/Ф/Module.bsl", edt) == ["Catalogs/Спр/Спр.mdo",
                                                                    "Catalogs/Спр/Forms/Ф/Form.form"]
    assert tp.descriptions("CommonForms/Ф/Module.bsl", edt) == ["CommonForms/Ф/Ф.mdo", "CommonForms/Ф/Form.form"]
    assert tp.descriptions("CommonModules/М/Module.bsl", edt) == ["CommonModules/М/М.mdo"]
    assert tp.descriptions("Catalogs/Спр/Forms/Ф/Ext/Form/Module.bsl", des) == [
        "Catalogs/Спр.xml", "Catalogs/Спр/Forms/Ф.xml", "Catalogs/Спр/Forms/Ф/Ext/Form.xml"]
    assert tp.descriptions("Documents/Д/Ext/ObjectModule.bsl", des) == ["Documents/Д.xml"]
    assert tp.project_path("Catalogs/Спр/Спр.mdo", edt) == "src/Catalogs/Спр/Спр.mdo"
    ext = tp.Project("", tp.DESIGNER, tp.eo.ExternalKind("ExternalDataProcessor", edt=False), "Обр")
    assert tp.descriptions("Обр/Forms/Ф/Ext/Form/Module.bsl", ext) == [
        "Обр.xml", "Обр/Forms/Ф.xml", "Обр/Forms/Ф/Ext/Form.xml"]
    assert tp.project_path("Обр.xml", ext) == "DataProcessors/Обр.xml"
    assert tp.project_path("Обр/Ext/ObjectModule.bsl", ext) == "DataProcessors/Обр/Ext/ObjectModule.bsl"
    ext_edt = tp.Project("", tp.EDT, tp.eo.ExternalKind("ExternalReport", edt=True), "О")
    assert tp.descriptions("ExternalReports/О/Forms/Ф/Module.bsl", ext_edt) == [
        "ExternalReports/О/О.mdo", "ExternalReports/О/Forms/Ф/Form.form"]
    assert tp.project_path("ExternalReports/О/ObjectModule.bsl", ext_edt) == "src/Reports/О/ObjectModule.bsl"


class ProjectAnalyzer:
    """analyze по мини-проекту: запоминает его состав и отвечает диагностиками копии модуля."""

    def __init__(self, module_rel: str, diags: list[tuple[int, str, str]], fail: bool = False):
        self.module_rel, self.diags, self.fail = module_rel, diags, fail
        self.files: set[str] = set()

    async def analyze(self, src_dir):
        root = Path(src_dir)
        self.files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        if self.fail:
            raise RuntimeError("analyze не создал отчёт (код возврата 1)")
        uri = (root / self.module_rel).resolve().as_uri()
        return [{"range": {"start": {"line": n - 1}}, "code": code, "severity": sev, "message": f"m{n}",
                 "_src_uri": uri} for n, code, sev in self.diags]


def _edt_repo(tmp_path):
    repo = tmp_path / "edt"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, "App/DT-INF/PROJECT.PMF", ["Manifest-Version: 1.0", "Runtime-Version: 8.3.27"], eol="\n")
    write(repo, "App/src/Configuration/Configuration.mdo", ["<mdclass:Configuration/>"], eol="\n")
    write(repo, "App/src/Catalogs/Спр/Спр.mdo", ["<mdclass:Catalog/>"], eol="\n")
    write(repo, "App/src/Catalogs/Спр/Forms/ФормаЭлемента/Form.form", ["<form:Form/>"], eol="\n")
    write(repo, "App/src/Catalogs/Чужой/Чужой.mdo", ["<mdclass:Catalog/>"], eol="\n")
    write(repo, EDT_FORM, ["Процедура А()", "КонецПроцедуры"], eol="\n")
    commit(repo)
    write(repo, EDT_FORM, ["Процедура А()", "\tБ = 1;", "КонецПроцедуры", "", "Процедура В()", "КонецПроцедуры"],
          eol="\n")
    return repo


def test_edt_task_lines_and_levels(tmp_path):
    repo = _edt_repo(tmp_path)
    fa = ProjectAnalyzer("src/Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl",
                         [(1, "Старое", "Error"),               # строка не задачи — нет
                          (2, "ОшибкаЗадачи", "Error"),          # → Fail
                          (5, "Стиль", "Warning"),               # → Warn
                          (6, "Шум", "Information"),             # info — нет
                          (2, "UnknownMember", "Error")])        # переназначен в info — нет
    r = asyncio.run(run_diagnostics_check(GitChangeSource(repo, "HEAD"), fa,
                                          severity_overrides=(("UnknownMember", "info"),)))
    assert [(f["ид"], f["уровень"], f["файл"], f["строка"]) for f in r["находки"]] == [
        ("ОшибкаЗадачи", "Fail", EDT_FORM, 2), ("Стиль", "Warn", EDT_FORM, 5)]
    assert r["пропущено"] == []
    assert {"DT-INF/PROJECT.PMF", "src/Configuration/Configuration.mdo", "src/Catalogs/Спр/Спр.mdo",
            "src/Catalogs/Спр/Forms/ФормаЭлемента/Form.form",
            "src/Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl"} == fa.files      # чужие объекты не нужны


def test_revision_and_exceptions(tmp_path):
    repo = _edt_repo(tmp_path)
    rev = commit(repo, "задача")
    write(repo, EDT_FORM, ["Процедура А()", "КонецПроцедуры"], eol="\n")          # рабочая копия откатана
    fa = ProjectAnalyzer("src/Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl", [(2, "ОшибкаЗадачи", "Error"),
                                                                            (5, "Стиль", "Warning")])
    exc = [RegistryException("Стиль", EDT_FORM, "m5", "так задумано")]
    base = git(repo, "rev-parse", "HEAD~1").strip()
    r = asyncio.run(run_diagnostics_check(GitChangeSource(repo, base, rev=rev), fa, exceptions=exc))
    assert [(f["ид"], f["строка"]) for f in r["находки"]] == [("ОшибкаЗадачи", 2)]   # текст ревизии, не диска
    assert [(x["ид"], x["основание"]) for x in r["исключены"]] == [("Стиль", "так задумано")]


def test_designer_and_failed_analyze_is_named(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, "Documents/Вендорский/Ext/ObjectModule.bsl",
          ["Процедура ПередЗаписью(Отказ, РежимЗаписи, РежимПроведения)", "\tЗначение = 2;", "КонецПроцедуры"])
    fa = ProjectAnalyzer("Documents/Вендорский/Ext/ObjectModule.bsl", [(2, "X", "Error")])
    r = asyncio.run(run_diagnostics_check(GitChangeSource(repo, "HEAD"), fa))
    assert [(f["ид"], f["строка"]) for f in r["находки"]] == [("X", 2)]
    assert {"Configuration.xml", "Documents/Вендорский.xml"} <= fa.files
    r = asyncio.run(run_diagnostics_check(GitChangeSource(repo, "HEAD"), ProjectAnalyzer("", [], fail=True)))
    assert r["находки"] == [] and "не создал отчёт" in r["пропущено"][0]["почему"]


def test_external_processing_dir_pair(tmp_path):
    base, target = tmp_path / "эталон", tmp_path / "копия"
    for d, body in ((base, ["Процедура П()", "КонецПроцедуры"]),
                    (target, ["Процедура П()", "\tА = 1;", "КонецПроцедуры"])):
        write(d, "Обр.xml", ['<MetaDataObject xmlns="x"><ExternalDataProcessor uuid="1"><Properties>'
                             "<DefaultForm>ExternalDataProcessor.Обр.Form.Ф</DefaultForm></Properties>"
                             "</ExternalDataProcessor></MetaDataObject>"])
        write(d, "Обр/Forms/Ф.xml", ["<MetaDataObject/>"])
        write(d, "Обр/Forms/Ф/Ext/Form.xml", ["<Form><v8:Type>cfg:ExternalDataProcessorObject.Обр</v8:Type></Form>"])
        write(d, "Обр/Forms/Ф/Ext/Form/Module.bsl", body)
    fa = ProjectAnalyzer("DataProcessors/Обр/Forms/Ф/Ext/Form/Module.bsl", [(2, "X", "Warning")])
    r = asyncio.run(run_diagnostics_check(DirPairChangeSource(base, target), fa))
    assert [(f["ид"], f["файл"], f["строка"]) for f in r["находки"]] == [("X", "Обр/Forms/Ф/Ext/Form/Module.bsl", 2)]
    assert {"Configuration.xml", "DataProcessors/Обр.xml", "DataProcessors/Обр/Forms/Ф/Ext/Form.xml"} <= fa.files


def test_cli_diagnostics_input_error(tmp_path):
    r = subprocess.run([sys.executable, "-m", "bsl_ls_mcp", "diagnostics", "--repo", str(tmp_path)],
                       capture_output=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"), "SYSTEMROOT": "C:\\Windows",
                                                           "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 2 and "git" in r.stderr.decode("utf-8")


def test_metadata_line():
    edt = ["<mdclass:InformationRegister>", "  <name>Курсы</name>", "  <dimensions>",
           "    <name>Валюта</name>", "  </dimensions>", "  <resources>", "    <name>Курсы</name>"]
    msg = 'Не указан флаг "Запрет незаполненных значений" у измерения "Валюта" метаданного "РегистрСведений.Курсы"'
    assert tp.metadata_line("\n".join(edt), msg) == 4
    assert tp.metadata_line("\n".join(edt), "Измените имя `Курсы`, чтобы оно не совпадало с родительским `Курсы`") == 7
    assert tp.metadata_line("<Properties><Name>Курсы</Name>", "Длина имени `РегистрСведений.Курсы`") == 1
    assert tp.metadata_line("\n".join(edt), "Общий модуль имеет неверный тип") is None


REG = "App/src/InformationRegisters/Курсы"


def _register_repo(tmp_path, new_dimension: bool):
    repo = tmp_path / "reg"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, "App/DT-INF/PROJECT.PMF", ["Manifest-Version: 1.0"], eol="\n")
    write(repo, "App/src/Configuration/Configuration.mdo", ["<mdclass:Configuration/>"], eol="\n")
    mdo = ["<mdclass:InformationRegister>", "  <name>Курсы</name>", "  <dimensions>", "    <name>Валюта</name>",
           "  </dimensions>"]
    write(repo, f"{REG}/Курсы.mdo", mdo + ["</mdclass:InformationRegister>"], eol="\n")
    write(repo, f"{REG}/ManagerModule.bsl", ["Процедура А()", "КонецПроцедуры"], eol="\n")
    commit(repo)
    if new_dimension:
        mdo += ["  <dimensions>", "    <name>Банк</name>", "  </dimensions>"]
    write(repo, f"{REG}/Курсы.mdo", mdo + ["  <comment>к</comment>", "</mdclass:InformationRegister>"], eol="\n")
    write(repo, f"{REG}/ManagerModule.bsl", ["Процедура А()", "\tБ = 1;", "КонецПроцедуры"], eol="\n")
    return repo


def test_metadata_diagnostic_goes_to_description(tmp_path):
    def msg(dim):
        return f'Не указан флаг "Запрет незаполненных значений" у измерения "{dim}" метаданного "Курсы"'

    class MetaAnalyzer(ProjectAnalyzer):
        async def analyze(self, src_dir):
            out = await super().analyze(src_dir)
            for d, dim in zip(out, ("Банк", "Валюта", "")):
                d["message"] = msg(dim) if dim else "Общий модуль имеет неверный тип"
            return out

    diags = [(1, "DenyIncompleteValues", "Warning"), (1, "DenyIncompleteValues", "Warning"),
             (1, "CommonModuleInvalidType", "Error")]
    fa = MetaAnalyzer("src/InformationRegisters/Курсы/ManagerModule.bsl", diags)
    r = asyncio.run(run_diagnostics_check(GitChangeSource(_register_repo(tmp_path, True), "HEAD"), fa))
    # новое измерение — на его строке; старое («Валюта») задача не трогала; без имени — первая правка
    assert [(f["ид"], f["файл"], f["строка"]) for f in r["находки"]] == [
        ("CommonModuleInvalidType", f"{REG}/Курсы.mdo", 6), ("DenyIncompleteValues", f"{REG}/Курсы.mdo", 7)]


def test_metadata_diagnostic_needs_description_change(tmp_path):
    repo = _register_repo(tmp_path, False)
    git(repo, "checkout", "--", f"{REG}/Курсы.mdo")                        # описание задача не правила
    fa = ProjectAnalyzer("src/InformationRegisters/Курсы/ManagerModule.bsl",
                         [(1, "DenyIncompleteValues", "Warning"), (2, "Х", "Error")])
    r = asyncio.run(run_diagnostics_check(GitChangeSource(repo, "HEAD"), fa))
    assert [(f["ид"], f["строка"]) for f in r["находки"]] == [("Х", 2)]


def test_query_objects():
    text = ('Запрос.Текст = "ВЫБРАТЬ * ИЗ Документ.Заказ КАК З\n'
            '| ЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.Курсы.СрезПоследних(, ) КАК К ПО ИСТИНА\n'
            '| ГДЕ З.Статус = ЗНАЧЕНИЕ(Перечисление.Статусы.Новый) И З.Ссылка В (ВЫБРАТЬ Р.Ссылка ИЗ документ.Заказ КАК Р)";\n'
            'Менеджер = Документы.Заказ; Т = Метаданные.Документ.Реквизиты; InformationRegister.Rates.Select();')
    assert tp.query_objects(text) == [("Documents", "Заказ"), ("InformationRegisters", "Курсы"),
                                      ("Enums", "Статусы"), ("InformationRegisters", "Rates")]
    edt, des = tp.Project("App/src/", tp.EDT), tp.Project("", tp.DESIGNER)
    assert tp.object_description("Documents", "Заказ", edt) == "Documents/Заказ/Заказ.mdo"
    assert tp.object_description("Documents", "Заказ", des) == "Documents/Заказ.xml"


def test_query_objects_in_project(tmp_path):
    repo = _edt_repo(tmp_path)
    write(repo, EDT_FORM, ["Процедура А()", '\tТекст = "ВЫБРАТЬ * ИЗ Справочник.Чужой КАК Ч '
                           'ЛЕВОЕ СОЕДИНЕНИЕ Документ.НетТакого КАК Н ПО ИСТИНА";', "КонецПроцедуры"], eol="\n")
    fa = ProjectAnalyzer("src/Catalogs/Спр/Forms/ФормаЭлемента/Module.bsl", [])
    asyncio.run(run_diagnostics_check(GitChangeSource(repo, "HEAD"), fa))
    assert "src/Catalogs/Чужой/Чужой.mdo" in fa.files                       # объект запроса — в проекте
    assert not any("НетТакого" in f for f in fa.files)                       # нет в источнике — не выдумываем
