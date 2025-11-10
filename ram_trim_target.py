"""
Aggressive Working Set Trimmer for target process (e.g., ComfyUI)
Author: eddy
"""

import sys
import time
import argparse
import ctypes
import ctypes.wintypes as wt
import psutil

# Win32 constants
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_QUOTA = 0x0100
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002

# Structures
class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]

class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wt.DWORD)]

class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wt.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]

# Win32 funcs
kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32
psapi = ctypes.windll.psapi

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
OpenProcess.restype = wt.HANDLE

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wt.HANDLE]
CloseHandle.restype = wt.BOOL

GetProcessMemoryInfo = psapi.GetProcessMemoryInfo
GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wt.DWORD]
GetProcessMemoryInfo.restype = wt.BOOL

EmptyWorkingSet = psapi.EmptyWorkingSet
EmptyWorkingSet.argtypes = [wt.HANDLE]
EmptyWorkingSet.restype = wt.BOOL

SetProcessWorkingSetSize = kernel32.SetProcessWorkingSetSize
SetProcessWorkingSetSize.argtypes = [wt.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
SetProcessWorkingSetSize.restype = wt.BOOL

OpenProcessToken = advapi32.OpenProcessToken
OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
OpenProcessToken.restype = wt.BOOL

LookupPrivilegeValueW = advapi32.LookupPrivilegeValueW
LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.POINTER(LUID)]
LookupPrivilegeValueW.restype = wt.BOOL

AdjustTokenPrivileges = advapi32.AdjustTokenPrivileges
AdjustTokenPrivileges.argtypes = [wt.HANDLE, wt.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES), wt.DWORD, ctypes.c_void_p, ctypes.c_void_p]
AdjustTokenPrivileges.restype = wt.BOOL

GetLastError = kernel32.GetLastError


def enable_privilege(name: str) -> bool:
    token = wt.HANDLE()
    if not OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(token)):
        return False
    try:
        luid = LUID()
        if not LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        if not AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None):
            return False
        # Check if actually enabled
        err = GetLastError()
        if err != 0:
            return False
        return True
    finally:
        CloseHandle(token)


def get_ws(handle) -> int:
    pmc = PROCESS_MEMORY_COUNTERS()
    pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    if GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
        return int(pmc.WorkingSetSize)
    return 0


def trim_pid(pid: int) -> dict:
    result = {"pid": pid, "ok": False, "before": 0, "after": 0, "freed": 0, "steps": {}}
    access = PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA
    h = OpenProcess(access, False, pid)
    if not h:
        result["steps"]["open"] = f"failed (err {GetLastError()})"
        return result
    result["steps"]["open"] = "ok"
    try:
        before = get_ws(h)
        result["before"] = before
        ok1 = bool(EmptyWorkingSet(h))
        result["steps"]["EmptyWorkingSet"] = "ok" if ok1 else f"failed (err {GetLastError()})"
        ok2 = bool(SetProcessWorkingSetSize(h, ctypes.c_size_t(-1), ctypes.c_size_t(-1)))
        result["steps"]["SetPWS"] = "ok" if ok2 else f"failed (err {GetLastError()})"
        time.sleep(0.5)
        after = get_ws(h)
        result["after"] = after
        result["freed"] = max(0, before - after)
        result["ok"] = ok1 or ok2
        return result
    finally:
        CloseHandle(h)


def pick_targets(auto: bool, pid: int | None):
    if pid:
        return [pid]
    if auto:
        # Prefer python.exe with comfyui in cmdline
        candidates = []
        for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
            try:
                name = (p.info.get("name") or "").lower()
                cmd = " ".join(map(str, p.info.get("cmdline") or []))
                mem = p.info["memory_info"].rss
                if name == "python.exe" and ("comfyui" in cmd.lower() or "main.py" in cmd.lower()):
                    candidates.append((mem, p.info["pid"]))
            except Exception:
                pass
        if not candidates:
            # Fallback: top 1 memory process
            top = sorted([(proc.memory_info().rss, proc.pid) for proc in psutil.process_iter(["pid", "memory_info"]) if proc.info.get("memory_info")], reverse=True)
            return [top[0][1]] if top else []
        # choose max mem python comfy
        candidates.sort(reverse=True)
        return [candidates[0][1]]
    return []


def fmt_gb(x: int) -> str:
    return f"{x / (1024**3):.2f} GB"


def main():
    parser = argparse.ArgumentParser(description="Aggressively trim target process working set")
    parser.add_argument("--pid", type=int, default=None, help="Target PID")
    parser.add_argument("--auto", action="store_true", help="Auto-pick ComfyUI (python.exe) or top memory process")
    args = parser.parse_args()

    print("\n================ Aggressive Working Set Trimmer ================")
    print("Author: eddy")

    # Enable privileges
    inc_ok = enable_privilege("SeIncreaseQuotaPrivilege")
    dbg_ok = enable_privilege("SeDebugPrivilege")
    print(f"Privileges: SeIncreaseQuotaPrivilege={'OK' if inc_ok else 'NO'}, SeDebugPrivilege={'OK' if dbg_ok else 'NO'}")

    targets = pick_targets(args.auto, args.pid)
    if not targets:
        print("No target process found. Use --pid <PID> or --auto")
        sys.exit(1)

    for tpid in targets:
        try:
            p = psutil.Process(tpid)
            name = p.name()
            cmd = " ".join(p.cmdline())
            print(f"\nTarget PID: {tpid}  Name: {name}\nCmd: {cmd}")
            res = trim_pid(tpid)
            print("  Steps:")
            for k, v in res["steps"].items():
                print(f"    {k:16}: {v}")
            print(f"  WorkingSet Before: {fmt_gb(res['before'])}")
            print(f"  WorkingSet After : {fmt_gb(res['after'])}")
            print(f"  Freed            : {fmt_gb(res['freed'])}")
        except psutil.NoSuchProcess:
            print(f"PID {tpid} no longer exists")

    print("\nDone.")

if __name__ == "__main__":
    main()
