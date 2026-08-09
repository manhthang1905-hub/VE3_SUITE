"""Kill zombie python processes using Windows API TerminateProcess directly."""
import ctypes
import ctypes.wintypes
import os
import subprocess

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_INFORMATION = 0x0400

kernel32 = ctypes.windll.kernel32

def force_kill_pid(pid):
    """Force kill process using Windows API."""
    if pid == os.getpid():
        return False
    handle = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        print(f"  PID {pid}: OpenProcess failed (error {ctypes.GetLastError()})")
        return False
    result = kernel32.TerminateProcess(handle, 1)
    kernel32.CloseHandle(handle)
    if result:
        print(f"  PID {pid}: KILLED")
        return True
    else:
        print(f"  PID {pid}: TerminateProcess failed (error {ctypes.GetLastError()})")
        return False

# Get all python/pythonw PIDs
out = subprocess.check_output(
    ["wmic", "process", "where", "Name='python.exe' OR Name='pythonw.exe'", 
     "get", "ProcessId,CreationDate,CommandLine", "/format:csv"],
    timeout=10, creationflags=0x08000000
).decode("utf-8", errors="replace")

my_pid = os.getpid()
print(f"My PID: {my_pid}")
print(f"Killing all python/pythonw except myself...\n")

killed = 0
for line in out.strip().splitlines():
    parts = line.strip().split(",")
    if len(parts) < 2:
        continue
    try:
        pid = int(parts[-1])
    except ValueError:
        continue
    if pid == my_pid:
        print(f"  PID {pid}: SKIP (self)")
        continue
    if force_kill_pid(pid):
        killed += 1

print(f"\nKilled: {killed}")

# Also kill chrome
subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], 
               capture_output=True, creationflags=0x08000000)
print("Chrome killed")
