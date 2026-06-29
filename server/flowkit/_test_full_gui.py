"""Test FlowKit GUI — auto-start, auto-restart, update, supervisor, cleanup."""
import sys, os, inspect
sys.path.insert(0, os.path.dirname(__file__))

import flowkit_gui

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

print("=== FlowKit GUI Full Test ===\n")

App = flowkit_gui.FlowKitGUI

# [1] All methods exist
print("[1] Methods exist")
methods = [
    '_on_start', '_on_stop', '_on_close',
    '_autostart_tick', '_cancel_autostart',
    '_scheduled_restart', '_exec_restart',
    '_supervise_processes', '_rotate_logs', '_rotate_log_file',
    '_can_restart',
    '_on_check_update', '_check_and_update_thread', '_do_update',
    '_get_remote_version',
    '_start_agent', '_start_gateway',
    '_kill_all_chrome', '_kill_flowkit_python',
]
for m in methods:
    check(m, hasattr(App, m))

# [2] Auto-start: checkbox controls countdown
print("\n[2] Auto-start controlled by checkbox")
src_init = inspect.getsource(App.__init__)
check("_autostart_var created", "_autostart_var" in src_init)
check("autostart_id conditional", "_autostart_var.get()" in src_init)

# [3] Auto-restart: _scheduled_restart → _on_stop → wait 20s → _on_start
print("\n[3] Auto-restart flow")
src_sched = inspect.getsource(App._scheduled_restart)
check("calls _on_stop", "_on_stop" in src_sched)
check("disables buttons", "state='disabled'" in src_sched or "disabled" in src_sched)
check("schedules _exec_restart", "_exec_restart" in src_sched)
check("saves _pending_restart_id", "_pending_restart_id" in src_sched)

src_drs = inspect.getsource(App._exec_restart)
check("uses subprocess.Popen (not os.execv)", "subprocess.Popen" in src_drs)
check("os._exit (clean exit)", "os._exit" in src_drs)
check("CREATE_NEW_PROCESS_GROUP", "CREATE_NEW_PROCESS_GROUP" in src_drs)
check("clears _pending_restart_id", "_pending_restart_id = None" in src_drs)

# [4] _on_stop: full cleanup
print("\n[4] _on_stop cleanup completeness")
src_stop = inspect.getsource(App._on_stop)
for item in [
    '_restart_timer_id', '_pending_restart_id',  # cancel timers
    '_started = False',                           # stop flag
    'proc.kill()',                                 # kill subprocesses
    'fh.close()',                                  # close log handles
    '_gateway_proc = None',                        # clear tracking
    '_gateway_tail_gen',                           # stop tail thread
    '_agent_log_fhs',                              # clear agent handles
    '_agent_procs',                                # clear agent procs
    'proxy.stop()',                                # stop IPv6 proxies
    'stop_ndp_keepalive',                          # stop NDP threads
    '_kill_all_chrome',                            # kill Chrome
    '_kill_flowkit_python',                        # kill Python procs
]:
    check(item, item in src_stop)

# [5] _on_stop cancels pending restart (user STOP during 20s wait)
print("\n[5] STOP cancels pending restart")
check("cancels _pending_restart_id in _on_stop", "_pending_restart_id" in src_stop)

# [6] Update: 1-click + cancel autostart
print("\n[6] Update flow")
src_update = inspect.getsource(App._on_check_update)
check("cancel autostart", "_cancel_autostart" in src_update)
check("1-click (check_and_update_thread)", "check_and_update_thread" in src_update)

# [7] Update: version check uses raw (no API rate limit)
print("\n[7] Version check — no API")
src_ver = inspect.getsource(App._get_remote_version)
check("uses raw.githubusercontent.com", "raw.githubusercontent" in src_ver)
check("NO api.github.com", "api.github.com" not in src_ver)
check("reads VERSION file", "/VERSION" in src_ver)

# [8] Git update: --depth=1 + checkout -f -B
print("\n[8] Git update fast")
src_do = inspect.getsource(App._do_update)
check("--depth=1", "--depth=1" in src_do)
check("checkout -f -B (works on fresh repo)", '"-f", "-B"' in src_do)
check("git init for VMs", '"git", "init"' in src_do)

# [9] Supervisor: auto-restart crashed gateway/agents
print("\n[9] Process supervisor")
src_sup = inspect.getsource(App._supervise_processes)
check("detect gateway crash", "_gateway_proc" in src_sup)
check("detect agent crash", "_agent_procs" in src_sup)
check("restart backoff (_can_restart)", "_can_restart" in src_sup)

src_can = inspect.getsource(App._can_restart)
check("max 3 per 5min", "3" in src_can and "300" in src_can)

# [10] _start_agent/_start_gateway: close old handle + log rotation
print("\n[10] Log handle management")
src_agent = inspect.getsource(App._start_agent)
check("close old agent handle", "_agent_log_fhs.pop" in src_agent)
check("rotate agent log", "_rotate_log_file" in src_agent)

src_gw = inspect.getsource(App._start_gateway)
check("close old gateway handle", "_gateway_log_fh" in src_gw)
check("rotate gateway log", "_rotate_log_file" in src_gw)
check("tail gen counter", "_gateway_tail_gen" in src_gw)

# [11] _start_all: abort if stopped mid-setup
print("\n[11] Startup abort on stop")
src_all = inspect.getsource(App._start_all)
occurrences = src_all.count("not self._started")
check(f"self._started checks ({occurrences}x)", occurrences >= 3)

# [12] _get_auto_version: reads VERSION file first (not git rev-list on shallow)
print("\n[12] Version detection")
src_ver2 = inspect.getsource(flowkit_gui._get_auto_version)
check("reads SUITE_ROOT/VERSION first", "SUITE_ROOT" in src_ver2)
check("skip shallow clone (count > 10)", "count > 10" in src_ver2 or "> 10" in src_ver2)

# [13] Startup kills leftover processes
print("\n[13] Startup cleanup (kill leftover from previous run)")
src_init_full = inspect.getsource(App.__init__)
check("_kill_all_chrome on startup", "_kill_all_chrome" in src_init_full)
check("_kill_flowkit_python on startup", "_kill_flowkit_python" in src_init_full)

print(f"\n{'='*50}")
print(f"Result: {passed} passed, {failed} failed")
if failed == 0:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED!")
    sys.exit(1)
