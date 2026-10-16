"""proc.py — is a pid alive, and what is its command line (stdlib only; shared by start.py, stop.py, views.py).

On Windows `os.kill(pid, 0)` is not a probe: any signal other than CTRL_C/CTRL_BREAK calls TerminateProcess, so a
liveness check would kill the process. There the checks use OpenProcess/GetExitCodeProcess and CIM instead.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        try:
            h = k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        except (OverflowError, ValueError):
            return False
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def cmdline(pid: int) -> str:
    if os.name == "nt":
        try:
            return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                   f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                                  capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError, ValueError):
            return ""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        pass
    try:
        return subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True,
                              timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
