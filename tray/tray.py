"""Трей-пультик для службы bsl-ls-mcp: лампочка статуса + старт/стоп/переиндекс/логи.
Сам демоном НЕ является — управляет NSSM-службой. Отдельный exe (--noconsole).

Лежит в корне бандла рядом с nssm.exe / install-service.ps1 / service.err.log.
Управляющие действия требуют прав администратора → запускаются через UAC (runas)."""
from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageFont

SERVICE = "bsl-ls-mcp"
_NOWIN = 0x08000000  # CREATE_NO_WINDOW — чтобы sc/nssm не мигали консолью


def _base() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


BASE = _base()
NSSM = str(BASE / "nssm.exe") if (BASE / "nssm.exe").exists() else "nssm"
LOG = BASE / "service.err.log"
INSTALL = BASE / "install-service.ps1"

# Статус-файл индекса (общий путь со службой) — ProgramData\bsl-ls-mcp\status.json
STATUS_FILE = (Path(os.environ.get("BSL_STATUS_FILE"))
               if os.environ.get("BSL_STATUS_FILE")
               else Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "bsl-ls-mcp" / "status.json")

# Адрес демона для проверки «слушает ли порт» (совпадает с дефолтами службы).
MCP_HOST = os.environ.get("BSL_MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("BSL_MCP_PORT", "8081"))

_state = "unknown"   # absent | stopped | starting | cold | indexing | ready


def _svc_running() -> str:
    """Состояние службы по sc query: running | stopped | absent."""
    try:
        out = subprocess.run(["sc", "query", SERVICE], capture_output=True, text=True,
                             creationflags=_NOWIN).stdout
    except Exception:
        return "absent"
    if "1060" in out or "не существует" in out.lower() or "does not exist" in out.lower():
        return "absent"
    if "RUNNING" in out:
        return "running"
    return "stopped"


def _index_state() -> str | None:
    """Состояние индекса из статус-файла: building | ready | idle | None."""
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8")).get("index")
    except Exception:
        return None


def _daemon_listening() -> bool:
    """Слушает ли демон порт. sc query показывает RUNNING сразу, как nssm поднял процесс,
    а uvicorn биндится позже — в это окно реиндекс молча не проходит (демон недоступен)."""
    try:
        with socket.create_connection((MCP_HOST, MCP_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def status() -> str:
    """Сводное состояние: absent | stopped | starting | cold | indexing | ready."""
    svc = _svc_running()
    if svc != "running":
        return svc  # absent | stopped
    if not _daemon_listening():
        return "starting"   # процесс есть, но порт ещё не слушает
    idx = _index_state()
    if idx == "building":
        return "indexing"
    if idx == "ready":
        return "ready"
    return "cold"   # демон отвечает, но индекса ещё нет (диагностики уже работают)


def _admin(exe: str, params: str) -> None:
    """Запустить команду с правами администратора (UAC)."""
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(BASE), 0)


def _notify(icon, text: str) -> None:
    try:
        icon.notify(text, "BSL LS MCP")
    except Exception:  # noqa: BLE001  (не все среды умеют уведомления)
        pass


def do_start(icon, item):  _admin(NSSM, f"start {SERVICE}")
def do_stop(icon, item):   _admin(NSSM, f"stop {SERVICE}")


def _reindex_worker(icon) -> None:
    """Ждём КОД ВОЗВРАТА --reindex и говорим правду. Раньше был Popen + except:pass —
    трей рапортовал «запущен» даже когда демон не отвечал (напр. клик сразу после
    старта службы, пока порт ещё не слушает) и статус молча оставался idle."""
    try:
        r = subprocess.run([str(BASE / "bsl-ls-mcp.exe"), "--reindex"],
                           creationflags=_NOWIN, cwd=str(BASE),
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        _notify(icon, "Реиндекс НЕ запущен: демон не ответил (таймаут 120 с)")
        return
    except Exception as exc:  # noqa: BLE001
        _notify(icon, f"Реиндекс НЕ запущен: {exc}")
        return
    if r.returncode == 0:
        _notify(icon, "Реиндекс запущен — индекс соберётся за пару минут")
    else:
        lines = [ln for ln in (r.stderr or "").strip().splitlines() if ln.strip()]
        why = lines[-1][:140] if lines else "демон недоступен на 127.0.0.1:8081?"
        _notify(icon, f"Реиндекс НЕ запущен: {why}")


def do_reindex(icon, item):
    # in-place реиндекс через MCP (bsl-ls-mcp.exe --reindex) — без UAC, демон не рвём.
    # В отдельном потоке: ждём результат, не морозя меню трея.
    threading.Thread(target=_reindex_worker, args=(icon,), daemon=True).start()
def do_logs(icon, item):
    if LOG.exists():
        os.startfile(str(LOG))  # noqa: S606
def do_install(icon, item):
    _admin("powershell", f'-ExecutionPolicy Bypass -File "{INSTALL}"')
def do_quit(icon, item):
    icon.visible = False
    icon.stop()


_ICON = 256          # рендерим крупно → ОС чётко уменьшает до размера трея
# Меньше букв = читабельнее в крошечном слоте трея (ОС всё равно ~16–24px).
_LINES = ("BSL",)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("arialbd.ttf", "segoeuib.ttf", "seguisb.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wh(d: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int, int, int]:
    left, top, right, bottom = d.textbbox((0, 0), text, font=font)
    return right - left, bottom - top, left, top


def _fit_font(d, lines, maxw: int, maxh: int):
    sz = 12
    font = _font(sz)
    while sz < 400:
        nf = _font(sz + 6)
        if any(_wh(d, t, nf)[0] > maxw or _wh(d, t, nf)[1] > maxh for t in lines):
            break
        sz += 6
        font = nf
    return font


def _icon_img(color) -> Image.Image:
    """Крупная надпись «BSL» во весь кадр, цвет = статус (жёлтый=индексирую и т.д.)."""
    canvas = Image.new("RGBA", (_ICON, _ICON), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    pad = int(_ICON * 0.04)
    maxh_per = (_ICON - 2 * pad) // len(_LINES)
    font = _fit_font(d, _LINES, _ICON - 2 * pad, maxh_per)
    gap = int(_ICON * 0.02)
    sizes = [_wh(d, t, font) for t in _LINES]
    total_h = sum(h for _, h, _, _ in sizes) + gap * (len(_LINES) - 1)
    y = (_ICON - total_h) // 2
    for text, (w, h, ox, oy) in zip(_LINES, sizes):
        x = (_ICON - w) // 2 - ox
        d.text((x, y - oy), text, font=font, fill=color)
        y += h + gap
    return canvas


_COLOR = {"absent": (150, 150, 150), "stopped": (225, 45, 45), "starting": (245, 140, 30),
          "cold": (245, 140, 30), "indexing": (245, 205, 30), "ready": (40, 200, 90)}
_LABEL = {"absent": "Не установлена", "stopped": "Остановлена", "starting": "Запускается…",
          "cold": "Поднята, индекса нет (диагностики работают)",
          "indexing": "Индексирую…", "ready": "Готов"}
# служба «живая» (для Стоп); реиндекс — только когда демон реально слушает порт
_RUNNING = ("starting", "cold", "indexing", "ready")
_REINDEXABLE = ("cold", "indexing", "ready")


def _menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(lambda i: f"Статус: {_LABEL.get(_state, '...')}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Запустить", do_start, enabled=lambda i: _state in ("stopped", "absent")),
        pystray.MenuItem("Остановить", do_stop, enabled=lambda i: _state in _RUNNING),
        pystray.MenuItem("Переиндексировать", do_reindex, enabled=lambda i: _state in _REINDEXABLE),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Открыть логи", do_logs),
        pystray.MenuItem("Установить службу", do_install, enabled=lambda i: _state == "absent"),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", do_quit),
    )


def _apply(icon: pystray.Icon) -> None:
    icon.icon = _icon_img(_COLOR.get(_state, _COLOR["stopped"]))
    icon.title = f"BSL LS — {_LABEL.get(_state, '')}"
    icon.update_menu()


def _poller(icon: pystray.Icon) -> None:
    global _state
    while getattr(icon, "_running_poll", True):
        new = status()
        if new != _state:
            _state = new
            _apply(icon)
        time.sleep(3)


def main() -> None:
    global _state
    _state = status()
    icon = pystray.Icon("bsl-ls-mcp", _icon_img(_COLOR.get(_state, _COLOR["stopped"])),
                        f"BSL LS — {_LABEL.get(_state, '')}", _menu())
    icon._running_poll = True
    threading.Thread(target=_poller, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
