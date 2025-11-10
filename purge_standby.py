"""
Purge Windows standby lists via NtSetSystemInformation
Author: eddy
"""

import ctypes
import time
import platform

if platform.system() != 'Windows':
    print('Windows only')
    raise SystemExit(0)

# Constants
SystemMemoryListInformation = 0x50

# Commands
MemoryCaptureAccessedBits = 0
MemoryCaptureAndResetAccessedBits = 1
MemoryEmptyWorkingSets = 2
MemoryFlushModifiedList = 3
MemoryPurgeStandbyList = 4
MemoryPurgeLowPriorityStandbyList = 5

ntdll = ctypes.windll.ntdll

def nt_status(status):
    return f"0x{status & 0xFFFFFFFF:08X}"

def issue(cmd: int):
    ul = ctypes.c_ulong(cmd)
    status = ntdll.NtSetSystemInformation(
        ctypes.c_int(SystemMemoryListInformation),
        ctypes.byref(ul),
        ctypes.c_ulong(ctypes.sizeof(ul))
    )
    return status

if __name__ == '__main__':
    print('\n=== Purging standby lists ===')
    for cmd, name in [
        (MemoryFlushModifiedList, 'FlushModifiedList'),
        (MemoryPurgeLowPriorityStandbyList, 'PurgeLowPriorityStandbyList'),
        (MemoryPurgeStandbyList, 'PurgeStandbyList'),
    ]:
        status = issue(cmd)
        ok = (status == 0)
        print(f'{name:28}: {"OK" if ok else "FAIL"} (status {nt_status(status)})')
        time.sleep(0.2)
    print('Done.')
