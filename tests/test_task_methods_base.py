"""task-methods и rules-check против базы: «новый» — имени нет в базе,
изменённое объявление — «изменён», файл с другими окончаниями строк — не правка задачи."""
from checks_support import commit, git, new_repo, registry, write

from bsl_ls_mcp.application.rules_check import run_rules_check, run_task_methods
from bsl_ls_mcp.infrastructure.dir_changes import DirPairChangeSource
from bsl_ls_mcp.infrastructure.git_changes import GitChangeSource

M = "CommonModules/Мод/Ext/Module.bsl"
BASE = ["Процедура Существующий(А) Экспорт", "\tВозврат;", "КонецПроцедуры", "",
        "Процедура Старый()", "КонецПроцедуры"]


def _methods(source):
    return sorted((m["метод"], m["статус"]) for m in run_task_methods(source))


def test_git_new_changed_renamed(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, M, BASE)
    commit(repo)
    write(repo, M, ["Процедура Существующий(А, Б = 0) Экспорт", "\tВозврат;", "КонецПроцедуры", "",
                    "Процедура Переименованный()", "КонецПроцедуры", "",
                    "Функция Добавленный()", "\tВозврат 1;", "КонецФункции"])
    assert _methods(GitChangeSource(repo, "HEAD")) == [
        ("Добавленный", "новый"), ("Переименованный", "новый"), ("Существующий", "изменён")]


def test_git_line_endings_only_is_not_a_task_change(tmp_path):
    repo = new_repo(tmp_path)
    vendor = ["Процедура Чужой()", "\tСообщить(\"x\");", "КонецПроцедуры"]
    write(repo, M, vendor, eol="\r\n")
    commit(repo)
    write(repo, M, vendor, eol="\n")                       # только окончания строк
    src = GitChangeSource(repo, "HEAD")
    assert run_task_methods(src) == []
    assert run_rules_check(src, registry())["находки"] == []   # чужой Сообщить() задаче не вменён


def test_git_renamed_file_compares_with_old_path(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, M, BASE)
    commit(repo)
    git(repo, "mv", "CommonModules/Мод", "CommonModules/Новое")
    write(repo, "CommonModules/Новое/Ext/Module.bsl", BASE[:3] + ["", "Процедура Старый()", "\tА = 1;", "КонецПроцедуры"])
    git(repo, "add", "-A")
    assert _methods(GitChangeSource(repo, "HEAD")) == [("Старый", "изменён")]


def test_new_file_methods_are_new(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, "CommonModules/Свой/Ext/Module.bsl", BASE)          # неотслеживаемый
    assert _methods(GitChangeSource(repo, "HEAD")) == [("Старый", "новый"), ("Существующий", "новый")]


def test_dir_pair_changed_declaration(tmp_path):
    base, target = tmp_path / "b", tmp_path / "t"
    write(base, "X/Ext/ObjectModule.bsl", BASE)
    write(target, "X/Ext/ObjectModule.bsl", ["Процедура Существующий(А, Б) Экспорт"] + BASE[1:], eol="\n")
    assert _methods(DirPairChangeSource(base, target)) == [("Существующий", "изменён")]
