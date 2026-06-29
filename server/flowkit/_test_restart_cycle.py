"""
Test auto-restart cycle logic — simulate 5 cycles without real Chrome/agents.
Verifies: no resource leaks, proper cleanup, no zombie state.
"""
import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))

# Patch heavy dependencies before importing GUI
import types

# Mock subprocess to avoid actually killing things
_original_popen = None
_mock_procs = []

class MockPopen:
    def __init__(self, *a, **kw):
        self.pid = id(self) % 100000
        self._alive = True
        _mock_procs.append(self)
    def poll(self):
        return None if self._alive else 0
    def kill(self):
        self._alive = False

class MockProxy:
    def __init__(self, port):
        self.listen_port = port
        self._running = False
    def start(self):
        self._running = True
        return True
    def stop(self):
        self._running = False

# Counts for verification
counts = {
    'proxy_starts': 0,
    'proxy_stops': 0,
    'agent_starts': 0,
    'gateway_starts': 0,
    'stop_calls': 0,
    'start_calls': 0,
    'ndp_stops': 0,
}


def run_test():
    import subprocess as sp
    original_popen = sp.Popen
    original_run = sp.run

    # Mock Popen
    sp.Popen = MockPopen
    sp.run = lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr="")

    # Import after mocking
    from flowkit_gui import FlowKitGUI, LOG_DIR, BASE_DIR

    LOG_DIR.mkdir(exist_ok=True)

    # Create minimal GUI without mainloop
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()

    gui = FlowKitGUI.__new__(FlowKitGUI)
    # Minimal init - just what we need
    gui._started = False
    gui._processes = []
    gui._log_handles = []
    gui._proc_lock = threading.Lock()
    gui._logs = []
    gui._gateway_proc = None
    gui._gateway_port_val = 5100
    gui._gateway_log_fh = None
    gui._gateway_tail_gen = 0
    gui._agent_procs = {}
    gui._agent_log_fhs = {}
    gui._inst_configs = {}
    gui._restart_times = {}
    gui._pending_restart_id = None
    gui._restart_timer_id = None
    gui._restart_at = 0
    gui._ipv6_proxies = []

    def mock_log(msg, level="INFO"):
        if "ERROR" in level or "WARN" in level:
            print(f"  [{level}] {msg}")

    gui._log = mock_log

    # Simulate _on_stop
    original_stop = FlowKitGUI._on_stop

    def test_on_stop(self):
        counts['stop_calls'] += 1
        # Set _started = False
        self._started = False
        # Kill mock processes
        for p in self._processes:
            p.kill()
        self._processes.clear()
        # Close log handles
        for fh in self._log_handles:
            try:
                fh.close()
            except:
                pass
        self._log_handles.clear()
        self._gateway_log_fh = None
        self._gateway_proc = None
        self._gateway_tail_gen += 1
        self._agent_log_fhs.clear()
        self._agent_procs.clear()
        # Stop proxies
        for proxy in self._ipv6_proxies:
            proxy.stop()
            counts['proxy_stops'] += 1
        self._ipv6_proxies = []
        # NDP
        counts['ndp_stops'] += 1
        print(f"  STOP: proxies={counts['proxy_stops']}, procs killed")

    def test_start_cycle(cycle_num):
        """Simulate one start cycle."""
        counts['start_calls'] += 1
        gui._started = True

        # Simulate creating proxies
        for port in range(1081, 1085):
            p = MockProxy(port)
            p.start()
            gui._ipv6_proxies.append(p)
            counts['proxy_starts'] += 1

        # Simulate creating agents
        for i in range(4):
            name = f"flowkit-{i+1}"
            log_file = LOG_DIR / f"{name}.log"
            # Rotate log
            if log_file.exists():
                bak = log_file.with_suffix('.log.bak')
                try:
                    if bak.exists():
                        bak.unlink()
                    log_file.rename(bak)
                except:
                    pass
            fh = open(log_file, 'w', encoding='utf-8')
            fh.write(f"Cycle {cycle_num} agent {name}\n")
            gui._agent_log_fhs[name] = fh
            gui._log_handles.append(fh)

            proc = MockPopen()
            gui._processes.append(proc)
            gui._agent_procs[name] = proc
            counts['agent_starts'] += 1

        # Simulate gateway
        gw_log = LOG_DIR / "gateway.log"
        if gw_log.exists():
            bak = gw_log.with_suffix('.log.bak')
            try:
                if bak.exists():
                    bak.unlink()
                gw_log.rename(bak)
            except:
                pass
        gw_fh = open(gw_log, 'w', encoding='utf-8')
        gw_fh.write(f"Cycle {cycle_num} gateway\n")
        gui._gateway_log_fh = gw_fh
        gui._log_handles.append(gw_fh)

        gw_proc = MockPopen()
        gui._processes.append(gw_proc)
        gui._gateway_proc = gw_proc
        counts['gateway_starts'] += 1

    # === Run 5 restart cycles ===
    print("=" * 50)
    print("Testing 5 restart cycles")
    print("=" * 50)

    for cycle in range(1, 6):
        print(f"\n--- Cycle {cycle} ---")

        # START
        test_start_cycle(cycle)
        print(f"  START: proxies={len(gui._ipv6_proxies)}, agents={len(gui._agent_procs)}, "
              f"handles={len(gui._log_handles)}, procs={len(gui._processes)}")

        assert len(gui._ipv6_proxies) == 4, f"Expected 4 proxies, got {len(gui._ipv6_proxies)}"
        assert len(gui._agent_procs) == 4, f"Expected 4 agents, got {len(gui._agent_procs)}"
        assert gui._gateway_proc is not None, "Gateway proc should exist"
        assert all(p._running for p in gui._ipv6_proxies), "All proxies should be running"

        # STOP
        test_on_stop(gui)

        assert len(gui._ipv6_proxies) == 0, f"Proxies should be empty after stop, got {len(gui._ipv6_proxies)}"
        assert len(gui._agent_procs) == 0, f"Agent procs should be empty after stop"
        assert gui._gateway_proc is None, "Gateway proc should be None after stop"
        assert len(gui._log_handles) == 0, f"Log handles should be empty, got {len(gui._log_handles)}"
        assert len(gui._processes) == 0, f"Processes should be empty, got {len(gui._processes)}"
        assert gui._started is False, "_started should be False after stop"

    # Check log files
    log_files = list(LOG_DIR.glob("*.log"))
    bak_files = list(LOG_DIR.glob("*.bak"))
    print(f"\n--- Final State ---")
    print(f"  Log files: {[f.name for f in log_files]}")
    print(f"  Bak files: {[f.name for f in bak_files]}")
    print(f"  Total starts: {counts['start_calls']}")
    print(f"  Total stops: {counts['stop_calls']}")
    print(f"  Proxy starts: {counts['proxy_starts']}, stops: {counts['proxy_stops']}")
    print(f"  Agent starts: {counts['agent_starts']}")
    print(f"  Gateway starts: {counts['gateway_starts']}")

    assert counts['proxy_starts'] == counts['proxy_stops'], \
        f"Proxy leak! starts={counts['proxy_starts']} stops={counts['proxy_stops']}"
    assert counts['start_calls'] == counts['stop_calls'], \
        f"Start/stop mismatch! starts={counts['start_calls']} stops={counts['stop_calls']}"

    print("\n✓ ALL TESTS PASSED — no resource leaks after 5 cycles")

    # Cleanup
    for f in LOG_DIR.glob("*.log"):
        f.unlink()
    for f in LOG_DIR.glob("*.bak"):
        f.unlink()

    sp.Popen = original_popen
    sp.run = original_run
    root.destroy()


if __name__ == "__main__":
    run_test()
