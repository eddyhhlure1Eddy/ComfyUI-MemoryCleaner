#!/usr/bin/env python3
"""
RAM Memory Cleaner Script
Author: eddy
Description: Check and force clean system RAM (not VRAM)
Usage: python cleanup_ram.py
"""

import gc
import os
import platform
import ctypes
import psutil
import time


def get_ram_info():
    """Get current RAM usage information"""
    process = psutil.Process(os.getpid())
    
    # Process RAM
    process_ram = process.memory_info().rss / (1024 ** 3)  # GB
    process_percent = process.memory_percent()
    
    # System RAM
    system_ram = psutil.virtual_memory()
    system_total = system_ram.total / (1024 ** 3)  # GB
    system_used = system_ram.used / (1024 ** 3)  # GB
    system_available = system_ram.available / (1024 ** 3)  # GB
    system_percent = system_ram.percent
    
    return {
        'process_gb': process_ram,
        'process_percent': process_percent,
        'system_total_gb': system_total,
        'system_used_gb': system_used,
        'system_available_gb': system_available,
        'system_percent': system_percent
    }


def print_ram_info(info, title="RAM INFO"):
    """Print RAM information"""
    print(f"\n{'='*70}")
    print(f"{title:^70}")
    print(f"{'='*70}")
    print(f"  Process RAM: {info['process_gb']:.3f} GB ({info['process_percent']:.2f}%)")
    print(f"  System Total: {info['system_total_gb']:.2f} GB")
    print(f"  System Used: {info['system_used_gb']:.2f} GB ({info['system_percent']:.1f}%)")
    print(f"  System Available: {info['system_available_gb']:.2f} GB")
    print(f"{'='*70}\n")


def force_gc_cleanup():
    """Force Python garbage collection"""
    print("🔹 Running Python garbage collection...")
    collected = []
    for i in range(3):
        count = gc.collect()
        collected.append(count)
        print(f"   Pass {i+1}: Collected {count} objects")
    return sum(collected)


def force_trim_ram_windows():
    """Force trim RAM on Windows using OS-level calls"""
    print("🔹 Trimming RAM (Windows OS calls)...")
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        hproc = kernel32.GetCurrentProcess()
        
        # EmptyWorkingSet - force pages to pagefile
        try:
            result = psapi.EmptyWorkingSet(hproc)
            print(f"   EmptyWorkingSet: {'Success' if result else 'Failed'}")
        except Exception as e:
            print(f"   EmptyWorkingSet: Failed ({e})")
        
        # SetProcessWorkingSetSize - trim working set
        try:
            result = kernel32.SetProcessWorkingSetSize(
                hproc, 
                ctypes.c_size_t(-1), 
                ctypes.c_size_t(-1)
            )
            print(f"   SetProcessWorkingSetSize: {'Success' if result else 'Failed'}")
        except Exception as e:
            print(f"   SetProcessWorkingSetSize: Failed ({e})")
        
        # _heapmin - minimize CRT heap
        try:
            ctypes.cdll.msvcrt._heapmin()
            print(f"   _heapmin (CRT heap): Success")
        except Exception as e:
            print(f"   _heapmin (CRT heap): Failed ({e})")
        
        return True
    except Exception as e:
        print(f"   Windows RAM trim failed: {e}")
        return False


def force_trim_ram_linux():
    """Force trim RAM on Linux using malloc_trim"""
    print("🔹 Trimming RAM (Linux malloc_trim)...")
    try:
        libc = ctypes.CDLL("libc.so.6")
        result = libc.malloc_trim(0)
        print(f"   malloc_trim: {'Success' if result else 'No memory freed'}")
        return True
    except Exception as e:
        print(f"   Linux RAM trim failed: {e}")
        return False


def cleanup_ram():
    """Main RAM cleanup function"""
    print("\n" + "="*70)
    print("RAM MEMORY CLEANER")
    print("="*70)
    
    # Get info before cleanup
    print("\n📊 CHECKING RAM BEFORE CLEANUP...")
    before = get_ram_info()
    print_ram_info(before, "BEFORE CLEANUP")
    
    # Execute cleanup
    print("🧹 EXECUTING RAM CLEANUP...\n")
    
    # 1. Python GC
    total_collected = force_gc_cleanup()
    
    # 2. OS-level trim
    os_name = platform.system().lower()
    if os_name == "windows":
        force_trim_ram_windows()
    elif os_name == "linux":
        force_trim_ram_linux()
    else:
        print(f"🔹 OS '{os_name}' not supported for deep trim, skipping...")
    
    # Wait for OS to release memory
    print("\n⏳ Waiting for OS to release memory...")
    time.sleep(2)
    
    # Get info after cleanup
    print("\n📊 CHECKING RAM AFTER CLEANUP...")
    after = get_ram_info()
    print_ram_info(after, "AFTER CLEANUP")
    
    # Calculate savings
    print("💾 CLEANUP RESULTS:")
    print(f"{'='*70}")
    
    process_saved = before['process_gb'] - after['process_gb']
    system_saved = before['system_used_gb'] - after['system_used_gb']
    system_freed_percent = ((before['system_used_gb'] - after['system_used_gb']) / before['system_total_gb']) * 100
    
    print(f"  Process RAM freed: {process_saved:.3f} GB")
    print(f"  System RAM freed: {system_saved:.3f} GB ({system_freed_percent:.2f}% of total)")
    print(f"  System Available increased: {after['system_available_gb'] - before['system_available_gb']:.3f} GB")
    print(f"  Python objects collected: {total_collected}")
    print(f"{'='*70}\n")
    
    # Status
    if process_saved > 0 or system_saved > 0:
        print("✅ RAM cleanup completed successfully!\n")
    else:
        print("⚠️  No significant RAM freed. Memory may be actively used.\n")


def check_ram_only():
    """Only check RAM without cleanup"""
    print("\n" + "="*70)
    print("RAM MEMORY CHECK (READ-ONLY)")
    print("="*70)
    
    info = get_ram_info()
    print_ram_info(info, "CURRENT RAM STATUS")
    
    # System-wide processes
    print("📋 TOP 10 MEMORY-CONSUMING PROCESSES:")
    print(f"{'='*70}")
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
        try:
            processes.append({
                'pid': proc.info['pid'],
                'name': proc.info['name'],
                'memory_gb': proc.info['memory_info'].rss / (1024 ** 3)
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    
    processes.sort(key=lambda x: x['memory_gb'], reverse=True)
    
    for i, proc in enumerate(processes[:10], 1):
        print(f"  {i:2d}. {proc['name']:<30} {proc['memory_gb']:>8.3f} GB (PID: {proc['pid']})")
    
    print(f"{'='*70}\n")


if __name__ == "__main__":
    import sys
    
    print("\n" + "="*70)
    print("RAM MEMORY CLEANER SCRIPT")
    print("Author: eddy")
    print("="*70)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        # Check only mode
        check_ram_only()
    else:
        # Cleanup mode
        print("\nOptions:")
        print("  1. Check RAM only (no cleanup)")
        print("  2. Force cleanup RAM")
        print("  3. Exit")
        
        try:
            choice = input("\nSelect option [1/2/3]: ").strip()
            
            if choice == "1":
                check_ram_only()
            elif choice == "2":
                cleanup_ram()
            elif choice == "3":
                print("\nExiting...\n")
                sys.exit(0)
            else:
                print("\n❌ Invalid option. Exiting...\n")
                sys.exit(1)
        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user. Exiting...\n")
            sys.exit(0)
