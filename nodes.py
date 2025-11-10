"""
ComfyUI Memory Cleaner Node
Author: eddy
Description: Real memory and VRAM cleanup node that actually works
"""

import gc
import torch
import psutil
import os
import platform
import ctypes
import sys
from pathlib import Path
import subprocess

# Try to import ComfyUI model management
try:
    import comfy.model_management as model_management
    COMFY_AVAILABLE = True
except ImportError:
    COMFY_AVAILABLE = False
    model_management = None

# Load C aggressive memory cleaner
C_CLEANER_AVAILABLE = False
c_aggressive_cleanup = None
c_free_result = None

try:
    current_dir = Path(__file__).parent
    if platform.system().lower() == "windows":
        dll_path = current_dir / "memory_cleaner.dll"
        if dll_path.exists():
            c_lib = ctypes.CDLL(str(dll_path))
            
            # Define CleanupResult structure
            class CleanupResult(ctypes.Structure):
                _fields_ = [
                    ("empty_working_set", ctypes.c_int),
                    ("set_working_set_size", ctypes.c_int),
                    ("heap_compact", ctypes.c_int),
                    ("virtual_free", ctypes.c_int),
                    ("working_set_before", ctypes.c_longlong),
                    ("working_set_after", ctypes.c_longlong),
                    ("freed_bytes", ctypes.c_longlong),
                ]
            
            # Set up function signatures
            c_lib.aggressive_cleanup.restype = ctypes.POINTER(CleanupResult)
            c_lib.aggressive_cleanup.argtypes = []
            
            c_lib.free_result.restype = None
            c_lib.free_result.argtypes = [ctypes.POINTER(CleanupResult)]
            
            c_aggressive_cleanup = c_lib.aggressive_cleanup
            c_free_result = c_lib.free_result
            C_CLEANER_AVAILABLE = True
except Exception as e:
    print(f"[MemoryCleaner] C cleaner not available: {e}")


# Define universal type that accepts everything
class AnyType(str):
    def __ne__(self, __value: object) -> bool:
        return False

any_type = AnyType("*")


class MemoryCleaner:
    """
    Real memory and VRAM cleanup node.
    Forces Python garbage collection and CUDA cache cleanup.
    """
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "aggressive_trim": ("BOOLEAN", {"default": True}),
                "enable_privileges": ("BOOLEAN", {"default": True}),
                "purge_standby": ("BOOLEAN", {"default": False}),
                "external_helper": ("BOOLEAN", {"default": True}),
                "skip_trim_if_c_low": ("BOOLEAN", {"default": True}),
                "min_c_free_gb": ("FLOAT", {"default": 20.0, "min": 0.0, "max": 200.0, "step": 1.0}),
                "anything": (any_type, {}),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            }
        }
    
    RETURN_TYPES = (any_type,)
    RETURN_NAMES = ("output",)
    FUNCTION = "cleanup_memory"
    CATEGORY = "system/memory"
    OUTPUT_NODE = True
    
    def get_memory_info(self):
        """Get current memory usage information"""
        process = psutil.Process(os.getpid())
        
        # RAM usage
        ram_used = process.memory_info().rss / (1024 ** 3)  # GB
        ram_percent = process.memory_percent()
        
        # System RAM
        system_ram = psutil.virtual_memory()
        system_ram_total = system_ram.total / (1024 ** 3)  # GB
        system_ram_used = system_ram.used / (1024 ** 3)  # GB
        system_ram_percent = system_ram.percent
        
        # VRAM usage
        vram_info = {}
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                vram_allocated = torch.cuda.memory_allocated(i) / (1024 ** 3)  # GB
                vram_reserved = torch.cuda.memory_reserved(i) / (1024 ** 3)  # GB
                vram_total = torch.cuda.get_device_properties(i).total_memory / (1024 ** 3)  # GB
                vram_info[f"GPU_{i}"] = {
                    "allocated": vram_allocated,
                    "reserved": vram_reserved,
                    "total": vram_total,
                    "percent": (vram_allocated / vram_total * 100) if vram_total > 0 else 0
                }
        
        return {
            "process_ram_gb": ram_used,
            "process_ram_percent": ram_percent,
            "system_ram_total_gb": system_ram_total,
            "system_ram_used_gb": system_ram_used,
            "system_ram_percent": system_ram_percent,
            "vram": vram_info
        }
    
    def _force_unload_models(self):
        """Force unload all models from ComfyUI cache"""
        if not COMFY_AVAILABLE:
            print("        ComfyUI model management not available")
            return 0
        
        unloaded = 0
        try:
            # Unload all models from VRAM
            if hasattr(model_management, 'unload_all_models'):
                model_management.unload_all_models()
                unloaded += 1
                print("        ✓ Unloaded all models from VRAM")
            
            # Soft empty cache
            if hasattr(model_management, 'soft_empty_cache'):
                model_management.soft_empty_cache()
                unloaded += 1
                print("        ✓ Soft emptied model cache")
            
            # Clean up model patches
            if hasattr(model_management, 'cleanup_models'):
                model_management.cleanup_models()
                unloaded += 1
                print("        ✓ Cleaned up model patches")
            
            # Force free memory
            if hasattr(model_management, 'free_memory'):
                freed = model_management.free_memory(
                    memory_required=1024*1024*1024*100,  # Request 100GB to force aggressive cleanup
                    device=model_management.get_torch_device()
                )
                try:
                    freed_gb = float(freed) / (1024**3)
                    print(f"        ✓ Freed {freed_gb:.2f} GB via model_management")
                except Exception:
                    print(f"        ✓ Freed via model_management: {freed}")
                unloaded += 1
            
        except Exception as e:
            print(f"        Model unload error: {e}")
        
        return unloaded
    
    def _clear_torch_cache(self):
        """Clear all PyTorch caches"""
        cleared = 0
        try:
            # Clear CUDA cache
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                cleared += 1
                
                # IPC collect
                try:
                    torch.cuda.ipc_collect()
                    cleared += 1
                except:
                    pass
                
                # Reset peak memory stats
                try:
                    for i in range(torch.cuda.device_count()):
                        torch.cuda.reset_peak_memory_stats(i)
                        torch.cuda.reset_accumulated_memory_stats(i)
                    cleared += 1
                except:
                    pass
            
            # Clear CPU tensors
            if hasattr(torch, '_C') and hasattr(torch._C, '_cuda_clearCublasWorkspaces'):
                try:
                    torch._C._cuda_clearCublasWorkspaces()
                    cleared += 1
                except:
                    pass
            
        except Exception as e:
            print(f"        Torch cache clear error: {e}")
        
        return cleared
    
    def _get_working_set_size(self):
        """Get current working set size"""
        if platform.system() != 'Windows':
            return 0
        try:
            import ctypes.wintypes
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            
            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ('cb', ctypes.wintypes.DWORD),
                    ('PageFaultCount', ctypes.wintypes.DWORD),
                    ('PeakWorkingSetSize', ctypes.c_size_t),
                    ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t),
                    ('PeakPagefileUsage', ctypes.c_size_t),
                ]
            
            hProcess = kernel32.GetCurrentProcess()
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            
            if psapi.GetProcessMemoryInfo(hProcess, ctypes.byref(pmc), pmc.cb):
                return pmc.WorkingSetSize
        except:
            pass
        # Fallback to psutil RSS
        try:
            return psutil.Process(os.getpid()).memory_info().rss
        except Exception:
            return 0
    
    def _enable_privileges(self):
        if platform.system() != 'Windows':
            return (False, False)
        try:
            import ctypes.wintypes as wt
            advapi32 = ctypes.windll.advapi32
            kernel32 = ctypes.windll.kernel32
            TOKEN_ADJUST_PRIVILEGES = 0x0020
            TOKEN_QUERY = 0x0008
            SE_PRIVILEGE_ENABLED = 0x00000002
            class LUID(ctypes.Structure):
                _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]
            class LUID_AND_ATTRIBUTES(ctypes.Structure):
                _fields_ = [("Luid", LUID), ("Attributes", wt.DWORD)]
            class TOKEN_PRIVILEGES(ctypes.Structure):
                _fields_ = [("PrivilegeCount", wt.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]
            token = wt.HANDLE()
            if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(token)):
                return (False, False)
            def enable(name):
                luid = LUID()
                if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
                    return False
                tp = TOKEN_PRIVILEGES()
                tp.PrivilegeCount = 1
                tp.Privileges[0].Luid = luid
                tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
                ok = advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
                err = kernel32.GetLastError()
                return bool(ok) and err == 0
            inc = enable("SeIncreaseQuotaPrivilege")
            dbg = enable("SeDebugPrivilege")
            kernel32.CloseHandle(token)
            return (inc, dbg)
        except:
            return (False, False)

    def _c_aggressive_cleanup(self):
        """Ultra-aggressive cleanup using Windows API (Pure Python + ctypes)"""
        if platform.system() != 'Windows':
            print("        Windows-only cleanup, skipping...")
            return None
        
        try:
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            hProcess = kernel32.GetCurrentProcess()
            
            # Get working set before
            ws_before = self._get_working_set_size()
            print(f"        Working Set Before: {ws_before / (1024**3):.3f} GB")
            
            # EmptyWorkingSet
            result1 = False
            try:
                result1 = psapi.EmptyWorkingSet(hProcess)
                if result1:
                    print("        EmptyWorkingSet: ✓ SUCCESS")
                else:
                    err = kernel32.GetLastError()
                    print(f"        EmptyWorkingSet: ✗ FAILED (err {err})")
            except Exception as e:
                print(f"        EmptyWorkingSet: ✗ {e}")
            
            # SetProcessWorkingSetSize with -1
            result2 = False
            try:
                result2 = kernel32.SetProcessWorkingSetSize(
                    hProcess,
                    ctypes.c_size_t(-1),
                    ctypes.c_size_t(-1)
                )
                if result2:
                    print("        SetProcessWorkingSetSize: ✓ SUCCESS")
                else:
                    err = kernel32.GetLastError()
                    print(f"        SetProcessWorkingSetSize: ✗ FAILED (err {err})")
            except Exception as e:
                print(f"        SetProcessWorkingSetSize: ✗ {e}")
            
            # _heapmin
            try:
                msvcrt = ctypes.cdll.msvcrt
                msvcrt._heapmin()
                print(f"        HeapMin: ✓ SUCCESS")
            except Exception as e:
                print(f"        HeapMin: ✗ {e}")
            
            # Wait for OS
            import time
            time.sleep(0.5)
            
            # Get working set after
            ws_after = self._get_working_set_size()
            freed = ws_before - ws_after
            
            print(f"        Working Set After:  {ws_after / (1024**3):.3f} GB")
            print(f"        FREED:              {freed / (1024**3):.3f} GB")
            # If both calls failed or freed <= 0, signal fallback by returning None
            if (not result1 and not result2) or (freed <= 0):
                return None
            return freed / (1024**3)
            
        except Exception as e:
            print(f"        Aggressive cleanup error: {e}")
            return None

    def _external_helper_trim(self):
        """Call external helper to trim this process working set"""
        try:
            script = Path(__file__).parent / "ram_trim_target.py"
            if not script.exists():
                return None
            cmd = [sys.executable, str(script), "--pid", str(os.getpid())]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            print("        External helper output (key lines):")
            for line in out.stdout.splitlines():
                s = line.strip()
                if s.startswith("EmptyWorkingSet") or s.startswith("SetPWS") or s.startswith("WorkingSet") or s.startswith("Freed"):
                    print(f"          {s}")
            # Parse freed GB
            freed_gb = None
            for line in out.stdout.splitlines():
                if line.strip().startswith("Freed") and line.strip().endswith("GB"):
                    try:
                        num = line.split(":",1)[1].strip().split()[0]
                        freed_gb = float(num)
                        break
                    except Exception:
                        pass
            return freed_gb
        except Exception as e:
            print(f"        External helper trim error: {e}")
            return None
    
    def _deep_trim_ram(self):
        """Use OS-level calls to return free memory to the OS."""
        try:
            sysname = platform.system().lower()
            if sysname == "windows":
                # Trim process working set and CRT heap
                try:
                    kernel32 = ctypes.windll.kernel32
                    psapi = ctypes.windll.psapi
                    hproc = kernel32.GetCurrentProcess()
                    try:
                        psapi.EmptyWorkingSet(hproc)
                    except Exception:
                        pass
                    try:
                        # Set min/max to -1 to force trim
                        kernel32.SetProcessWorkingSetSize(hproc, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
                    except Exception:
                        pass
                    try:
                        ctypes.cdll.msvcrt._heapmin()
                    except Exception:
                        pass
                except Exception:
                    pass
            elif sysname == "linux":
                # Return free heap pages to OS (glibc)
                try:
                    libc = ctypes.CDLL("libc.so.6")
                    try:
                        libc.malloc_trim(0)
                    except Exception:
                        pass
                except Exception:
                    pass
            else:
                # Other OS: no-op
                pass
        except Exception:
            # Never raise from deep trim path
            pass

    def _get_disk_free_gb(self, path):
        try:
            usage = psutil.disk_usage(path)
            return usage.free / (1024**3)
        except Exception:
            return None

    def _purge_standby_lists(self):
        if platform.system() != 'Windows':
            return {}
        try:
            ntdll = ctypes.windll.ntdll
            SystemMemoryListInformation = 0x50
            cmds = [
                (3, "FlushModifiedList"),
                (5, "PurgeLowPriorityStandbyList"),
                (4, "PurgeStandbyList"),
            ]
            results = {}
            for cmd, name in cmds:
                ul = ctypes.c_ulong(cmd)
                status = ntdll.NtSetSystemInformation(ctypes.c_int(SystemMemoryListInformation), ctypes.byref(ul), ctypes.c_ulong(ctypes.sizeof(ul)))
                results[name] = int(status)
            return results
        except Exception as e:
            return {"error": str(e)}
    
    def cleanup_memory(self, aggressive_trim=True, enable_privileges=True, purge_standby=False, external_helper=True, skip_trim_if_c_low=True, min_c_free_gb=20.0, anything=None, unique_id=None):
        """Execute memory cleanup"""
        
        print("\n" + "="*80)
        print("[MemoryCleaner] Starting memory cleanup...")
        print("="*80)
        
        # Get memory info before cleanup
        before = self.get_memory_info()
        
        print("\n📊 BEFORE CLEANUP:")
        print(f"  Process RAM: {before['process_ram_gb']:.2f} GB ({before['process_ram_percent']:.1f}%)")
        print(f"  System RAM: {before['system_ram_used_gb']:.2f} / {before['system_ram_total_gb']:.2f} GB ({before['system_ram_percent']:.1f}%)")
        
        if before['vram']:
            print(f"\n  VRAM Usage:")
            for gpu_name, info in before['vram'].items():
                print(f"    {gpu_name}: {info['allocated']:.2f} GB allocated, {info['reserved']:.2f} GB reserved, {info['total']:.2f} GB total ({info['percent']:.1f}%)")
        
        # Execute cleanup
        print("\n🧹 EXECUTING CLEANUP...")
        
        # 0. Force unload ComfyUI models FIRST
        print("  [0/7] Force unloading ComfyUI models...")
        unloaded_count = self._force_unload_models()
        if unloaded_count > 0:
            print(f"        ✓ Executed {unloaded_count} model unload operations")
        
        if enable_privileges and platform.system() == 'Windows':
            inc, dbg = self._enable_privileges()
            print(f"        Privileges: SeIncreaseQuotaPrivilege={'OK' if inc else 'NO'}, SeDebugPrivilege={'OK' if dbg else 'NO'}")
        
        # 1. Force Python garbage collection (multiple passes for thorough cleanup)
        print("  [1/7] Running Python garbage collection (pass 1)...")
        collected_1 = gc.collect()
        print(f"        Collected {collected_1} objects")
        
        print("  [2/7] Running Python garbage collection (pass 2)...")
        collected_2 = gc.collect()
        print(f"        Collected {collected_2} objects")
        
        print("  [3/7] Running Python garbage collection (pass 3)...")
        collected_3 = gc.collect()
        print(f"        Collected {collected_3} objects")
        
        total_collected = collected_1 + collected_2 + collected_3
        
        # 2. Clear all PyTorch caches
        print("  [4/7] Clearing PyTorch caches...")
        torch_cleared = self._clear_torch_cache()
        if torch_cleared > 0:
            print(f"        ✓ Cleared {torch_cleared} torch cache operations")
        
        # 3. C-level aggressive cleanup (if available)
        can_os_trim = True
        if skip_trim_if_c_low and platform.system() == 'Windows':
            free_c = self._get_disk_free_gb('C:\\')
            if free_c is not None and free_c < float(min_c_free_gb):
                can_os_trim = False
                print(f"  [5/7] Skipping OS trim: C:\\ free {free_c:.1f} GB < {float(min_c_free_gb):.1f} GB (avoid pagefile growth)")
        if aggressive_trim and can_os_trim:
            print("  [5/7] C-level aggressive cleanup...")
            c_freed = self._c_aggressive_cleanup()
            if c_freed is not None:
                print(f"        ✓ C-level cleanup freed {c_freed:.3f} GB")
            else:
                if external_helper:
                    print("        Using external helper to trim working set...")
                    ext_freed = self._external_helper_trim()
                    if ext_freed is not None:
                        print(f"        ✓ External helper freed {ext_freed:.3f} GB")
                    else:
                        print("        External helper not available or failed.")
                else:
                    print("        Using Python-level RAM trim...")
                    self._deep_trim_ram()
                    print("        ✓ Python RAM trim completed")
        elif aggressive_trim and not can_os_trim:
            print("  [5/7] Aggressive trim disabled due to low C: free space")
        else:
            print("  [5/7] Skipping aggressive trim by user setting")
        
        # 4. Final CUDA synchronization
        if torch.cuda.is_available():
            print("  [6/7] Synchronizing CUDA devices...")
            torch.cuda.synchronize()
            print("        ✓ CUDA devices synchronized")
        else:
            print("  [6/7] CUDA not available, skipping...")
        
        # 5. Final GC pass
        print("  [7/7] Final garbage collection pass...")
        final_collected = gc.collect()
        print(f"        Collected {final_collected} objects")
        total_collected += final_collected
        
        if purge_standby:
            print("\n  Purging standby lists...")
            purge_res = self._purge_standby_lists()
            if purge_res:
                print(f"        Standby purge results: {purge_res}")

        # Get memory info after cleanup
        after = self.get_memory_info()
        
        print("\n📊 AFTER CLEANUP:")
        print(f"  Process RAM: {after['process_ram_gb']:.2f} GB ({after['process_ram_percent']:.1f}%)")
        print(f"  System RAM: {after['system_ram_used_gb']:.2f} / {after['system_ram_total_gb']:.2f} GB ({after['system_ram_percent']:.1f}%)")
        
        if after['vram']:
            print(f"\n  VRAM Usage:")
            for gpu_name, info in after['vram'].items():
                print(f"    {gpu_name}: {info['allocated']:.2f} GB allocated, {info['reserved']:.2f} GB reserved, {info['total']:.2f} GB total ({info['percent']:.1f}%)")
        
        # Calculate savings
        print("\n💾 CLEANUP RESULTS:")
        ram_saved = before['process_ram_gb'] - after['process_ram_gb']
        system_ram_saved = before['system_ram_used_gb'] - after['system_ram_used_gb']
        print(f"  Process RAM freed: {ram_saved:.3f} GB")
        print(f"  System RAM freed: {system_ram_saved:.3f} GB")
        print(f"  Python objects collected: {total_collected}")
        
        if before['vram'] and after['vram']:
            for gpu_name in before['vram'].keys():
                vram_saved = before['vram'][gpu_name]['allocated'] - after['vram'][gpu_name]['allocated']
                reserved_saved = before['vram'][gpu_name]['reserved'] - after['vram'][gpu_name]['reserved']
                print(f"  {gpu_name} VRAM freed: {vram_saved:.3f} GB (allocated), {reserved_saved:.3f} GB (reserved)")
        
        print("\n" + "="*80)
        print("[MemoryCleaner] ✓ Cleanup completed successfully")
        print("="*80 + "\n")
        
        # Pass through the input data
        if anything is not None:
            return (anything,)
        else:
            return (None,)


class MemoryStatus:
    """
    Display current memory and VRAM status without cleanup
    """
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "anything": (any_type, {}),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            }
        }
    
    RETURN_TYPES = (any_type, "STRING")
    RETURN_NAMES = ("output", "status_text")
    FUNCTION = "show_status"
    CATEGORY = "system/memory"
    OUTPUT_NODE = True
    
    def show_status(self, anything=None, unique_id=None):
        """Display memory status"""
        
        process = psutil.Process(os.getpid())
        
        # RAM info
        ram_used = process.memory_info().rss / (1024 ** 3)
        ram_percent = process.memory_percent()
        
        system_ram = psutil.virtual_memory()
        system_ram_total = system_ram.total / (1024 ** 3)
        system_ram_used = system_ram.used / (1024 ** 3)
        system_ram_percent = system_ram.percent
        
        status_lines = []
        status_lines.append("="*60)
        status_lines.append("MEMORY STATUS")
        status_lines.append("="*60)
        status_lines.append(f"Process RAM: {ram_used:.2f} GB ({ram_percent:.1f}%)")
        status_lines.append(f"System RAM: {system_ram_used:.2f} / {system_ram_total:.2f} GB ({system_ram_percent:.1f}%)")
        
        # VRAM info
        if torch.cuda.is_available():
            status_lines.append("")
            status_lines.append("VRAM Status:")
            for i in range(torch.cuda.device_count()):
                gpu_name = torch.cuda.get_device_name(i)
                vram_allocated = torch.cuda.memory_allocated(i) / (1024 ** 3)
                vram_reserved = torch.cuda.memory_reserved(i) / (1024 ** 3)
                vram_total = torch.cuda.get_device_properties(i).total_memory / (1024 ** 3)
                vram_percent = (vram_allocated / vram_total * 100) if vram_total > 0 else 0
                status_lines.append(f"  GPU {i} ({gpu_name}):")
                status_lines.append(f"    Allocated: {vram_allocated:.2f} GB")
                status_lines.append(f"    Reserved: {vram_reserved:.2f} GB")
                status_lines.append(f"    Total: {vram_total:.2f} GB")
                status_lines.append(f"    Usage: {vram_percent:.1f}%")
        else:
            status_lines.append("")
            status_lines.append("VRAM: No CUDA devices available")
        
        status_lines.append("="*60)
        
        status_text = "\n".join(status_lines)
        
        print("\n" + status_text + "\n")
        
        if anything is not None:
            return (anything, status_text)
        else:
            return (None, status_text)


# Node registration
NODE_CLASS_MAPPINGS = {
    "MemoryCleaner": MemoryCleaner,
    "MemoryStatus": MemoryStatus,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MemoryCleaner": "Memory Cleaner (Force)",
    "MemoryStatus": "Memory Status",
}
