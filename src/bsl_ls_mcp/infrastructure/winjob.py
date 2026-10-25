"""Windows Job Object с KILL_ON_JOB_CLOSE: дочерний java умирает ВМЕСТЕ с родителем
при любом завершении (Ctrl+C, taskkill /F, падение, стоп службы) — без сирот.
На не-Windows — no-op. Best-effort: ошибки молча игнорируются."""
from __future__ import annotations

import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100

    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _k.CreateJobObjectW.restype = wintypes.HANDLE
    _k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k.OpenProcess.restype = wintypes.HANDLE
    _k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k.CloseHandle.argtypes = [wintypes.HANDLE]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _job = None  # один job на процесс, держится всю жизнь родителя

    def _ensure_job():
        global _job
        if _job is not None:
            return _job
        h = _k.CreateJobObjectW(None, None)
        if not h:
            return None
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _k.SetInformationJobObject(
            h, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
        _job = h
        return h

    def assign_kill_on_close(pid: int) -> None:
        """Привязать процесс pid к job — он умрёт, когда умрёт родитель."""
        try:
            job = _ensure_job()
            if not job:
                return
            hp = _k.OpenProcess(_PROCESS_TERMINATE | _PROCESS_SET_QUOTA, False, pid)
            if hp:
                _k.AssignProcessToJobObject(job, hp)
                _k.CloseHandle(hp)
        except Exception:
            pass

else:
    def assign_kill_on_close(pid: int) -> None:  # noqa: D103
        return
