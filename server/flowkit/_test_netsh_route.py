"""Test netsh add route behavior — does duplicate add fail?"""
import subprocess

iface = "Ethernet"

r1 = subprocess.run(f'netsh interface ipv6 add route ::/0 "{iface}" ::1',
                     shell=True, capture_output=True, text=True, timeout=10)
print(f"1st add: rc={r1.returncode} | {r1.stdout.strip()} {r1.stderr.strip()}")

r2 = subprocess.run(f'netsh interface ipv6 add route ::/0 "{iface}" ::1',
                     shell=True, capture_output=True, text=True, timeout=10)
print(f"2nd add (same gw): rc={r2.returncode} | {r2.stdout.strip()} {r2.stderr.strip()}")

r3 = subprocess.run(f'netsh interface ipv6 add route ::/0 "{iface}" ::2',
                     shell=True, capture_output=True, text=True, timeout=10)
print(f"3rd add (diff gw): rc={r3.returncode} | {r3.stdout.strip()} {r3.stderr.strip()}")

r4 = subprocess.run('netsh interface ipv6 show route', shell=True, capture_output=True, text=True)
for line in r4.stdout.splitlines():
    if '::/0' in line:
        print(f"  ROUTE: {line.strip()}")

subprocess.run(f'netsh interface ipv6 delete route ::/0 "{iface}" ::1', shell=True, capture_output=True)
subprocess.run(f'netsh interface ipv6 delete route ::/0 "{iface}" ::2', shell=True, capture_output=True)
print("Cleaned up")
