"""Test VE3 auto-start + auto-restart logic."""
import sys, os, inspect
sys.path.insert(0, os.path.dirname(__file__))

import ve3_gui

passed = 0
failed = 0

def check(name, condition):
    global passed, failed
    if condition:
        print(f"  OK  {name}")
        passed += 1
    else:
        print(f"  FAIL {name}")
        failed += 1

print("=== VE3 Auto-Restart Tests ===\n")

# Methods exist
print("[1] Methods exist")
for m in ['_schedule_auto_restart', '_do_auto_restart', '_exec_restart',
          '_auto_start_tick', '_on_check_update', 'toggle_queue_worker',
          '_on_close', '_kill_extension_instances']:
    check(m, hasattr(ve3_gui.VE3App, m))

# _do_auto_restart has same cleanup as _on_close
print("\n[2] _do_auto_restart cleanup = _on_close cleanup")
src_close = inspect.getsource(ve3_gui.VE3App._on_close)
src_restart = inspect.getsource(ve3_gui.VE3App._do_auto_restart)
for c in ['queue_stop_requested', '_kill_extension_instances', '_kill_own_child_processes',
          'music_stop_requested']:
    check(f"_on_close has {c}", c in src_close)
    check(f"_do_auto_restart has {c}", c in src_restart)

# Update cancels auto-start
print("\n[3] Update cancels auto-start")
src_update = inspect.getsource(ve3_gui.VE3App._on_check_update)
check("_auto_start_countdown = 0", "_auto_start_countdown = 0" in src_update)

# STOP cancels auto-restart
print("\n[4] STOP cancels auto-restart")
src_toggle = inspect.getsource(ve3_gui.VE3App.toggle_queue_worker)
check("cancel _pending_restart_id", "_pending_restart_id" in src_toggle)
check("after_cancel", "after_cancel" in src_toggle)

# Boot sets 30s
print("\n[5] Boot countdown = 30s")
src_boot = inspect.getsource(ve3_gui.VE3App._boot)
check("_auto_start_countdown = 30", "_auto_start_countdown = 30" in src_boot)

# Auto-start tick schedules 12h restart
print("\n[6] Auto-start schedules 12h restart")
src_tick = inspect.getsource(ve3_gui.VE3App._auto_start_tick)
check("calls _schedule_auto_restart", "_schedule_auto_restart" in src_tick)

# Schedule sets 12h timer
print("\n[7] Schedule = 12h")
src_sched = inspect.getsource(ve3_gui.VE3App._schedule_auto_restart)
check("hours = 12", "hours = 12" in src_sched)
check("_do_auto_restart callback", "_do_auto_restart" in src_sched)

# _exec_restart uses os.execv
print("\n[8] Restart uses os.execv")
src_exec = inspect.getsource(ve3_gui.VE3App._exec_restart)
check("os.execv", "os.execv" in src_exec)

# Manual RUN also schedules restart
print("\n[9] Manual RUN schedules auto-restart")
check("_schedule_auto_restart in toggle_queue_worker", "_schedule_auto_restart" in src_toggle)

print(f"\n{'='*40}")
print(f"Result: {passed} passed, {failed} failed")
if failed == 0:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED!")
    sys.exit(1)
