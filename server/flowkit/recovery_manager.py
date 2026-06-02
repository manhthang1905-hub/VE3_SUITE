"""
FlowKit Recovery Manager — autonomous 403/429 recovery with escalation levels.

Triggered by:
  - 403 reCAPTCHA / IP block: consecutive_403 >= 3 → cooling → trigger_recovery
  - 429 rate limit: retries exhausted → mark_quota_exhausted → trigger_recovery
  - Self-heal: extension disconnected / Chrome dead → trigger_self_heal

Recovery Levels (escalation chain, runs in ONE call):
  L1: Reset captcha — clear cookies/cache + reload Flow + re-capture token (~45s)
      Fixes: stale session, captcha cache, cookie corruption
  L2: Rotate IPv6 — get new IP from pool + restart Chrome with new proxy (~60s)
      Fixes: IP-based rate limit / block by Google
  L3: Full restart — clean profile (keep extension) + restart Chrome + re-login (~90s)
      Fixes: deep session corruption, Chrome state issues

If L1 fails → escalate to L2. If L2 fails → escalate to L3.
If ALL fail → log "manual intervention needed".
State persists: next trigger_recovery starts at last failed level.
On success at ANY level → clear cooldown, reset counters, instance back online.

Account for re-login read from (in order):
  1. _accounts (set by GUI via set_accounts)
  2. config/.flow_accounts.json (saved by GUI on startup)
  3. config/flowkit_gui.json accounts field (gateway standalone mode)
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent


@dataclass
class InstanceRecoveryState:
    """Per-instance recovery tracking."""
    name: str
    recovery_level: int = 0
    recovering: bool = False
    last_recovery_time: float = 0
    recovery_count: int = 0
    current_ipv6: str = ""
    current_seed: int = 0
    level_attempts: Dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0, 3: 0})

    def reset(self):
        self.recovery_level = 0
        self.level_attempts = {1: 0, 2: 0, 3: 0}
        self.last_recovery_time = 0

    def next_level(self) -> int:
        if self.recovery_level >= 3:
            return 3
        self.recovery_level += 1
        return self.recovery_level


RECOVERY_CONFIG_DEFAULTS = {
    "level1_max_attempts": 2,
    "level2_max_attempts": 2,
    "level3_max_attempts": 3,
    "min_recovery_interval": 30,
    "extension_reconnect_timeout": 30,
    "chrome_restart_delay": 5,
}


class RecoveryManager:
    """Manages 403 recovery for all FlowKit instances."""

    def __init__(
        self,
        config: dict,
        instances_config: List[dict],
        on_cooldown_clear: Optional[Callable] = None,
    ):
        self.config = config
        self.instances_config = {cfg["name"]: cfg for cfg in instances_config}
        self.on_cooldown_clear = on_cooldown_clear

        recovery_cfg = config.get("recovery", {})
        self.level1_max = recovery_cfg.get("level1_max_attempts", RECOVERY_CONFIG_DEFAULTS["level1_max_attempts"])
        self.level2_max = recovery_cfg.get("level2_max_attempts", RECOVERY_CONFIG_DEFAULTS["level2_max_attempts"])
        self.level3_max = recovery_cfg.get("level3_max_attempts", RECOVERY_CONFIG_DEFAULTS["level3_max_attempts"])
        self.min_interval = recovery_cfg.get("min_recovery_interval", RECOVERY_CONFIG_DEFAULTS["min_recovery_interval"])
        self.reconnect_timeout = recovery_cfg.get("extension_reconnect_timeout", RECOVERY_CONFIG_DEFAULTS["extension_reconnect_timeout"])
        self.restart_delay = recovery_cfg.get("chrome_restart_delay", RECOVERY_CONFIG_DEFAULTS["chrome_restart_delay"])

        self.states: Dict[str, InstanceRecoveryState] = {}
        for name in self.instances_config:
            self.states[name] = InstanceRecoveryState(name=name)

        self._ipv6_client = None
        ipv6_cfg = config.get("ipv6", {})
        if ipv6_cfg.get("enabled"):
            try:
                from ipv6_pool_client import IPv6PoolClient
                self._ipv6_client = IPv6PoolClient(
                    api_url=ipv6_cfg.get("pool_url", "http://192.168.88.146:8765"),
                    log_func=lambda msg: logger.info("[IPv6] %s", msg),
                )
                if self._ipv6_client.ping():
                    logger.info("[Recovery] IPv6 pool connected")
                else:
                    logger.warning("[Recovery] IPv6 pool not reachable")
                    self._ipv6_client = None
            except Exception as e:
                logger.warning("[Recovery] IPv6 client init failed: %s", e)

        self._recovery_tasks: Dict[str, asyncio.Task] = {}
        self._accounts: Dict[str, dict] = {}

        # Fixed Account mode
        fa_cfg = config.get("fixed_account", {})
        self._fa_enabled = fa_cfg.get("enabled", False)
        self._fa_concurrent = fa_cfg.get("concurrent", 2)
        self._fa_cooldown = fa_cfg.get("cooldown_seconds", 300)
        self._fa_states: Dict[str, str] = {}  # name → "active"/"standby"/"cooling"
        self._fa_cooling_until: Dict[str, float] = {}  # name → timestamp
        self._fa_all_names: List[str] = []  # ordered list of ALL instance names
        self._fa_swap_lock = asyncio.Lock()

        if self._fa_enabled:
            self._fa_all_names = [cfg["name"] for cfg in instances_config]
            for i, name in enumerate(self._fa_all_names):
                if i < self._fa_concurrent:
                    self._fa_states[name] = "active"
                else:
                    self._fa_states[name] = "standby"
            logger.info("[Recovery] Fixed Account mode: %d total, %d concurrent",
                        len(self._fa_all_names), self._fa_concurrent)

        # Account pool for rotation (like chrome_pool.py get_next_account)
        self._all_accounts: List[dict] = []
        self._account_usage: Dict[str, int] = {}
        self._account_last_assigned: Dict[str, int] = {}
        self._account_assign_seq = 0

    def set_accounts(self, accounts: List[dict], instances: List[dict]):
        """Map accounts to instances for auto-login during recovery."""
        for i, inst in enumerate(instances):
            if not inst.get("enabled", True):
                continue
            if accounts:
                acc = accounts[i % len(accounts)]
                self._accounts[inst["name"]] = {
                    "id": acc.get("email", acc.get("id", "")),
                    "password": acc.get("password", ""),
                    "totp_secret": acc.get("totp_secret", ""),
                }

    def set_account_pool(self, accounts: List[dict]):
        """Set full account pool for rotation on 403."""
        self._all_accounts = []
        for acc in accounts:
            self._all_accounts.append({
                "id": acc.get("email", acc.get("id", "")),
                "password": acc.get("password", ""),
                "totp_secret": acc.get("totp_secret", ""),
            })
        logger.info("[Recovery] Account pool: %d accounts", len(self._all_accounts))

    def get_next_account(self, current_email: str) -> Optional[dict]:
        """Pick least-used account, different from current. Like chrome_pool.py."""
        if not self._all_accounts:
            return None

        # Get emails currently assigned to instances
        in_use = {acc.get("id", "") for acc in self._accounts.values()}

        best = None
        best_score = None
        for acc in self._all_accounts:
            email = acc["id"]
            if email == current_email:
                continue
            if email in in_use:
                continue
            usage = self._account_usage.get(email, 0)
            last_assigned = self._account_last_assigned.get(email, -1)
            score = (usage, last_assigned, email)
            if best_score is None or score < best_score:
                best_score = score
                best = acc

        # Fallback: allow in-use accounts (all accounts busy — rotate anyway)
        if not best:
            for acc in self._all_accounts:
                email = acc["id"]
                if email == current_email:
                    continue
                usage = self._account_usage.get(email, 0)
                last_assigned = self._account_last_assigned.get(email, -1)
                score = (usage, last_assigned, email)
                if best_score is None or score < best_score:
                    best_score = score
                    best = acc

        if best:
            self._account_assign_seq += 1
            self._account_usage[best["id"]] = self._account_usage.get(best["id"], 0) + 1
            self._account_last_assigned[best["id"]] = self._account_assign_seq
            logger.info("[Recovery] Next account: %s (used=%dx)",
                        best["id"], self._account_usage[best["id"]])

        return best

    def _get_account(self, instance_name: str) -> Optional[dict]:
        if instance_name in self._accounts:
            return self._accounts[instance_name]
        # Fallback 1: read from file saved by GUI
        try:
            import json as _json
            accounts_file = BASE_DIR / "config" / ".flow_accounts.json"
            if accounts_file.exists():
                data = _json.loads(accounts_file.read_text(encoding="utf-8"))
                acc = data.get(instance_name)
                if acc:
                    return acc
        except Exception:
            pass
        # Fallback 2: parse from flowkit_gui.json (gateway standalone mode)
        try:
            import json as _json
            gui_file = BASE_DIR / "config" / "flowkit_gui.json"
            if gui_file.exists():
                gui_data = _json.loads(gui_file.read_text(encoding="utf-8"))
                raw = gui_data.get("accounts", "")
                if raw:
                    accounts = []
                    for line in raw.strip().split("\n"):
                        parts = line.strip().split("|")
                        if len(parts) >= 2:
                            accounts.append({
                                "id": parts[0].strip(),
                                "password": parts[1].strip(),
                                "totp_secret": parts[2].strip() if len(parts) >= 3 else "",
                            })
                    if accounts:
                        instances_cfg = list(self.instances_config.keys())
                        idx = instances_cfg.index(instance_name) if instance_name in instances_cfg else 0
                        acc = accounts[idx % len(accounts)]
                        logger.info("[Recovery] %s: account from flowkit_gui.json: %s",
                                    instance_name, acc["id"])
                        return acc
        except Exception:
            pass
        return None

    def on_instance_success(self, instance_name: str):
        """Called when an instance completes a request successfully."""
        state = self.states.get(instance_name)
        if state:
            state.reset()

    def trigger_recovery(self, instance_name: str):
        """Start recovery for an instance that just entered cooldown.

        This is called from the gateway when mark_403() triggers cooldown.
        Recovery runs as an async background task.
        """
        if instance_name not in self.states:
            return

        state = self.states[instance_name]
        if state.recovering:
            logger.info("[Recovery] %s already recovering, skip", instance_name)
            return

        now = time.time()
        if now - state.last_recovery_time < self.min_interval:
            logger.info("[Recovery] %s too soon since last recovery (%.0fs), skip",
                        instance_name, now - state.last_recovery_time)
            return

        task = asyncio.create_task(self._run_recovery(instance_name))
        task.add_done_callback(lambda t, n=instance_name: self._recovery_tasks.pop(n, None))
        self._recovery_tasks[instance_name] = task

    def trigger_self_heal(self, instance_name: str, rotate_ipv6: bool = False):
        """Self-heal: instance is down (not healthy/extension dead).

        Skips L1 captcha reset (useless when Chrome is dead) and goes
        straight to full Chrome restart — y het startup.
        """
        if instance_name not in self.states:
            return

        state = self.states[instance_name]
        if state.recovering:
            return

        now = time.time()
        if now - state.last_recovery_time < self.min_interval:
            return

        task = asyncio.create_task(self._run_self_heal(instance_name, rotate_ipv6))
        task.add_done_callback(lambda t, n=instance_name: self._recovery_tasks.pop(n, None))
        self._recovery_tasks[instance_name] = task

    async def _run_self_heal(self, instance_name: str, rotate_ipv6: bool = False):
        """Self-heal: full Chrome restart (skip L1 captcha reset)."""
        state = self.states[instance_name]
        state.recovering = True
        state.last_recovery_time = time.time()
        state.recovery_count += 1

        logger.info("[SelfHeal] %s: full restart (rotate_ipv6=%s, attempt #%d)",
                    instance_name, rotate_ipv6, state.recovery_count)

        try:
            new_ip = ""
            if rotate_ipv6:
                new_ip = self._rotate_ipv6(instance_name, "self_heal")
                if not new_ip and self._ipv6_client:
                    logger.warning("[SelfHeal] %s: NO IPv6 available from pool — PAUSING instance. "
                                   "Will retry when pool has IPs.", instance_name)
                    state.recovering = False
                    return
                self._rotate_account_for_instance(instance_name)

            success = await self._restart_chrome_instance(instance_name, new_ipv6=new_ip)

            if success:
                logger.info("[SelfHeal] %s: restart OK", instance_name)
                state.reset()
                if self.on_cooldown_clear:
                    self.on_cooldown_clear(instance_name)
            else:
                logger.warning("[SelfHeal] %s: restart FAILED", instance_name)

        except Exception as e:
            logger.exception("[SelfHeal] %s error: %s", instance_name, e)
        finally:
            state.recovering = False

    async def _run_recovery(self, instance_name: str):
        """Execute recovery escalation for an instance."""
        state = self.states[instance_name]
        state.recovering = True
        state.last_recovery_time = time.time()
        state.recovery_count += 1

        level = state.next_level()
        logger.info("[Recovery] %s starting LEVEL %d recovery (attempt #%d)",
                    instance_name, level, state.recovery_count)

        try:
            success = False

            # Try current level, escalate on failure
            if level <= 1 and state.level_attempts[1] < self.level1_max:
                state.level_attempts[1] += 1
                success = await self._level1_reset_captcha(instance_name)
                if success:
                    pass  # done
                else:
                    logger.info("[Recovery] %s Level 1 failed, escalating to L2", instance_name)
                    state.recovery_level = 2
                    level = 2

            if not success and level <= 2 and state.level_attempts[2] < self.level2_max:
                state.level_attempts[2] += 1
                # Rotate account before L2 restart
                self._rotate_account_for_instance(instance_name)
                success = await self._level2_rotate_ipv6(instance_name)
                if not success:
                    logger.info("[Recovery] %s Level 2 failed, escalating to L3", instance_name)
                    state.recovery_level = 3
                    level = 3

            if not success and self._fa_enabled:
                # Fixed Account mode: swap Chrome instead of L3 re-login
                logger.info("[Recovery] %s L2 failed → Fixed Account SWAP", instance_name)
                success = await self._fa_swap_chrome(instance_name)
            elif not success and state.level_attempts[3] < self.level3_max:
                state.recovery_level = 3
                state.level_attempts[3] += 1
                # Rotate account before L3 restart
                self._rotate_account_for_instance(instance_name)
                success = await self._level3_restart_chrome(instance_name)

            if not success and all(
                state.level_attempts[lv] >= mx
                for lv, mx in [(1, self.level1_max), (2, self.level2_max), (3, self.level3_max)]
            ):
                logger.warning(
                    "[Recovery] %s ALL recovery levels exhausted. "
                    "Instance needs manual intervention.",
                    instance_name,
                )

            if success:
                logger.info("[Recovery] %s recovery DONE at level %d", instance_name, state.recovery_level)
                if self.on_cooldown_clear:
                    self.on_cooldown_clear(instance_name)
            else:
                logger.warning("[Recovery] %s recovery FAILED at level %d", instance_name, state.recovery_level)

        except Exception as e:
            logger.exception("[Recovery] %s error: %s", instance_name, e)
        finally:
            state.recovering = False

    async def _fa_swap_chrome(self, instance_name: str) -> bool:
        """Fixed Account swap: replace 403'd Chrome with a standby Chrome.

        1. Soft-clean 403'd profile (keep login + extension)
        2. Kill 403'd Chrome
        3. Mark cooling (300s)
        4. Find next standby instance
        5. Rotate IPv6 for new instance
        6. Start Chrome for standby → extension connects → gateway routes to it
        """
        async with self._fa_swap_lock:
            cfg = self.instances_config.get(instance_name)
            if not cfg:
                return False

            loop = asyncio.get_event_loop()

            # 1. Soft-clean profile
            try:
                from launcher import soft_clean_chrome_profile
                chrome_dir = (BASE_DIR / cfg["chrome_path"]).resolve().parent.parent.parent
                await loop.run_in_executor(None, soft_clean_chrome_profile, chrome_dir)
                logger.info("[FA-Swap] %s: soft-cleaned profile", instance_name)
            except Exception as e:
                logger.warning("[FA-Swap] %s: soft-clean error: %s", instance_name, e)

            # 2. Kill Chrome
            try:
                from launcher import kill_chrome
                await loop.run_in_executor(None, kill_chrome, cfg)
                logger.info("[FA-Swap] %s: Chrome killed", instance_name)
            except Exception as e:
                logger.warning("[FA-Swap] %s: kill error: %s", instance_name, e)

            # 3. Mark cooling
            self._fa_states[instance_name] = "cooling"
            self._fa_cooling_until[instance_name] = time.time() + self._fa_cooldown
            logger.info("[FA-Swap] %s: cooling for %ds", instance_name, self._fa_cooldown)

            # 4. Find next standby
            standby_name = None
            for name in self._fa_all_names:
                if self._fa_states.get(name) == "standby":
                    standby_name = name
                    break

            # If no standby, wait for first cooldown to expire
            if not standby_name:
                logger.info("[FA-Swap] No standby available — waiting for cooldown...")
                earliest_name = None
                earliest_time = float("inf")
                for name, until in self._fa_cooling_until.items():
                    if until < earliest_time and name != instance_name:
                        earliest_time = until
                        earliest_name = name
                if not earliest_name:
                    earliest_name = instance_name
                    earliest_time = self._fa_cooling_until.get(instance_name, time.time() + self._fa_cooldown)

                wait_secs = max(0, earliest_time - time.time())
                if wait_secs > 0:
                    logger.info("[FA-Swap] Waiting %.0fs for %s cooldown...", wait_secs, earliest_name)
                    await asyncio.sleep(wait_secs)

                # Soft-clean the expired instance and reuse it
                standby_name = earliest_name
                try:
                    standby_cfg = self.instances_config.get(standby_name, {})
                    standby_chrome = (BASE_DIR / standby_cfg["chrome_path"]).resolve().parent.parent.parent
                    await loop.run_in_executor(None, soft_clean_chrome_profile, standby_chrome)
                except Exception:
                    pass
                self._fa_states[standby_name] = "standby"

            # 5. Rotate IPv6 for standby
            new_ip = ""
            if self._ipv6_client:
                new_ip = self._rotate_ipv6(standby_name, "fa_swap")

            # 6. Start Chrome for standby
            standby_cfg = self.instances_config.get(standby_name)
            if not standby_cfg:
                logger.warning("[FA-Swap] No config for %s", standby_name)
                return False

            success = await self._restart_chrome_instance(standby_name, new_ipv6=new_ip,
                                                          clean=False, login=False)
            if success:
                self._fa_states[standby_name] = "active"
                logger.info("[FA-Swap] %s → %s: swap OK", instance_name, standby_name)
                return True
            else:
                logger.warning("[FA-Swap] %s → %s: swap FAILED", instance_name, standby_name)
                self._fa_states[standby_name] = "cooling"
                self._fa_cooling_until[standby_name] = time.time() + self._fa_cooldown
                return False

    async def _level1_reset_captcha(self, instance_name: str) -> bool:
        """Level 1: Reset captcha via extension API."""
        logger.info("[Recovery] %s Level 1: resetting captcha via extension", instance_name)

        cfg = self.instances_config.get(instance_name)
        if not cfg:
            return False

        api_port = cfg["api_port"]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"http://127.0.0.1:{api_port}/api/reset-captcha")
                result = resp.json()

            if result.get("success"):
                logger.info("[Recovery] %s Level 1: captcha reset OK, waiting for extension reconnect...", instance_name)
                connected = await self._wait_extension_connect(api_port, timeout=45)
                if connected:
                    await asyncio.sleep(10)
                    logger.info("[Recovery] %s Level 1: extension reconnected + stabilized", instance_name)
                    return True
                else:
                    logger.warning("[Recovery] %s Level 1: extension not connected after 45s", instance_name)
                    return False
            else:
                logger.warning("[Recovery] %s Level 1: reset failed: %s",
                               instance_name, result.get("error", "unknown"))
                return False
        except Exception as e:
            logger.warning("[Recovery] %s Level 1 error: %s", instance_name, e)
            return False

    def _rotate_account_for_instance(self, instance_name: str):
        """Rotate to a different account for this instance (on 403/429 recovery)."""
        current = self._accounts.get(instance_name, {})
        current_email = current.get("id", "")
        new_acc = self.get_next_account(current_email)
        if new_acc and new_acc["id"] != current_email:
            logger.info("[Recovery] %s: ACCOUNT ROTATION %s → %s",
                        instance_name, current_email, new_acc["id"])
            self._accounts[instance_name] = new_acc
            # Update .flow_accounts.json
            try:
                import json as _json
                accounts_file = BASE_DIR / "config" / ".flow_accounts.json"
                if accounts_file.exists():
                    data = _json.loads(accounts_file.read_text(encoding="utf-8"))
                else:
                    data = {}
                data[instance_name] = new_acc
                accounts_file.write_text(_json.dumps(data), encoding="utf-8")
            except Exception:
                pass
        else:
            logger.info("[Recovery] %s: no other account available, keeping %s",
                        instance_name, current_email)

    def _rotate_ipv6(self, instance_name: str, reason: str = "403_recovery") -> str:
        """Get new IPv6 from pool. Returns new IP or empty string."""
        if not self._ipv6_client:
            return ""
        state = self.states[instance_name]
        old_ip = state.current_ipv6
        try:
            result = (self._ipv6_client.rotate_ip(old_ip, reason=reason, worker=instance_name)
                      if old_ip else self._ipv6_client.get_ip(worker=instance_name))
            if result:
                new_ip = result["ip"]
                state.current_ipv6 = new_ip
                logger.info("[Recovery] %s: got new IPv6 %s", instance_name, new_ip)
                return new_ip
        except Exception as e:
            logger.warning("[Recovery] %s: IPv6 rotate error: %s", instance_name, e)
        return ""

    async def _level2_rotate_ipv6(self, instance_name: str) -> bool:
        """Level 2: Get new IPv6, restart Chrome from scratch."""
        logger.info("[Recovery] %s Level 2: rotating IPv6", instance_name)
        if not self._ipv6_client:
            logger.info("[Recovery] %s Level 2: no IPv6 pool, skip to Level 3", instance_name)
            return False
        loop = asyncio.get_running_loop()
        new_ip = await loop.run_in_executor(None, self._rotate_ipv6, instance_name, "403_L2")
        if not new_ip:
            return False
        return await self._restart_chrome_instance(instance_name, new_ipv6=new_ip)

    async def _level3_restart_chrome(self, instance_name: str) -> bool:
        """Level 3: Get NEW IPv6 + full restart from scratch."""
        new_ip = self._rotate_ipv6(instance_name, "403_L3")
        if not new_ip:
            new_ip = self.states[instance_name].current_ipv6 or ""
        logger.info("[Recovery] %s Level 3: full restart (%s)",
                    instance_name, f"IPv6={new_ip}" if new_ip else "fingerprint-only")
        return await self._restart_chrome_instance(instance_name, new_ipv6=new_ip)

    async def _restart_chrome_instance(self, instance_name: str, new_ipv6: str = "",
                                       clean: bool = True, login: bool = True) -> bool:
        """Restart Chrome — full or partial.

        login=True (default): kill → IPv6 → setup_chrome (login+Flow) → start Chrome → CDP
        login=False (FA swap): kill → IPv6 → start Chrome directly (already logged in) → CDP
        """
        cfg = self.instances_config.get(instance_name)
        if not cfg:
            return False

        try:
            from launcher import kill_chrome, resolve_path, start_chrome
            loop = asyncio.get_running_loop()

            api_port = cfg["api_port"]
            chrome_dir = resolve_path(cfg["chrome_path"]).parent.parent.parent
            ext_dir = resolve_path(cfg["extension_dir"])
            debug_port = 19200 + (api_port - 8100)
            proxy_port = 1081 + (api_port - 8100)

            # Step 1: Kill Chrome (with timeout to prevent hang)
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: kill_chrome(instance_name)),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                logger.warning("[Recovery] %s: kill_chrome timeout (30s), forcing continue", instance_name)
            await asyncio.sleep(8)  # Windows needs 5-10s to release debug port

            # Step 2: Full IPv6 setup + update proxy
            proxy_arg = ""
            if new_ipv6:
                _state = self.states.get(instance_name)
                old_ipv6 = _state.current_ipv6 if _state and _state.current_ipv6 != new_ipv6 else ""
                gateway = ""
                try:
                    # Compute gateway from IP
                    gateway = ':'.join(new_ipv6.split(':')[:4]) + '::1'
                except Exception:
                    pass
                # Full setup: delete old → add new → route → NDP ping
                try:
                    from ipv6_proxy import setup_ipv6_on_interface, start_ndp_keepalive
                    await loop.run_in_executor(None, lambda: setup_ipv6_on_interface(
                        new_ipv6, gateway, "Ethernet", old_ipv6,
                        lambda msg: logger.info("[Recovery] %s: %s", instance_name, msg)))
                    start_ndp_keepalive(new_ipv6, gateway, proxy_port,
                                       lambda msg: logger.info("[Recovery] %s: %s", instance_name, msg))
                except Exception as e:
                    logger.warning("[Recovery] %s: IPv6 setup error: %s", instance_name, e)
                # Update proxy binding
                try:
                    for px in getattr(self, '_proxies', []):
                        if hasattr(px, 'listen_port') and px.listen_port == proxy_port:
                            px.set_ipv6(new_ipv6)
                            break
                except Exception:
                    pass
                try:
                    Path(f".ipv6_override_{proxy_port}").write_text(new_ipv6)
                except Exception:
                    pass
                await asyncio.sleep(2)
                proxy_arg = f"socks5://127.0.0.1:{proxy_port}"
            elif cfg.get("ipv6"):
                proxy_arg = f"socks5://127.0.0.1:{proxy_port}"

            # Step 3: setup_chrome — retry with different account if login fails
            if login:
                account = self._get_account(instance_name)
                from chrome_setup import setup_chrome
                ok = False
                for _login_try in range(3):
                    try:
                        ok = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda: setup_chrome(
                                    chrome_dir=chrome_dir, ext_dir=ext_dir, port=debug_port,
                                    account=account, proxy_arg=proxy_arg,
                                    log_func=lambda msg: logger.info("[Recovery] %s: %s", instance_name, msg),
                                    instance_name=instance_name,
                                ),
                            ),
                            timeout=180,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("[Recovery] %s: setup_chrome TIMEOUT (180s)", instance_name)
                        ok = False
                    if ok:
                        break
                    # Login failed → try different account
                    if account and self._all_accounts:
                        old_email = account.get("id", "?")
                        self._rotate_account_for_instance(instance_name)
                        account = self._get_account(instance_name)
                        logger.warning("[Recovery] %s: setup fail → doi account %s → %s",
                                       instance_name, old_email, account.get("id", "?") if account else "?")
                        await loop.run_in_executor(None, lambda: kill_chrome(instance_name))
                        await asyncio.sleep(3)
                    else:
                        break
                if not ok:
                    logger.warning("[Recovery] %s: setup_chrome FAILED after %d attempts", instance_name, _login_try + 1)
                    return False
            else:
                logger.info("[Recovery] %s: skip login (Fixed Account — already logged in)", instance_name)

            # Step 4: Start Chrome subprocess (extension connects to agent)
            cfg_start = {**cfg, "ipv6": new_ipv6} if new_ipv6 else cfg
            proc = await loop.run_in_executor(
                None,
                lambda: start_chrome(cfg_start, new_fingerprint=False, clean=False),
            )
            if not proc:
                logger.warning("[Recovery] %s: Chrome subprocess failed", instance_name)
                return False
            logger.info("[Recovery] %s: Chrome started (PID %d)", instance_name, proc.pid)

            # Step 5: Wait for extension
            await asyncio.sleep(self.restart_delay)
            connected = await self._wait_extension_connect(api_port, timeout=self.reconnect_timeout)
            if not connected:
                logger.warning("[Recovery] %s: extension not connected within %ds",
                               instance_name, self.reconnect_timeout)
                return False

            # Step 6: Apply CDP (zoom + fingerprint + tab guard)
            try:
                from chrome_setup import apply_chrome_cdp
                await loop.run_in_executor(
                    None,
                    lambda: apply_chrome_cdp(
                        debug_port=debug_port, ext_dir=ext_dir,
                        instance_name=instance_name,
                        log_func=lambda msg: logger.info("[Recovery] %s: %s", instance_name, msg),
                    ),
                )
            except Exception as e:
                logger.warning("[Recovery] %s: CDP error: %s", instance_name, e)

            logger.info("[Recovery] %s: READY", instance_name)
            return True

        except Exception as e:
            logger.exception("[Recovery] %s restart error: %s", instance_name, e)
            return False

    async def _ensure_project(self, api_port: int) -> bool:
        """Ask extension to click 'Dự án mới' if not already in a project."""
        try:
            async with httpx.AsyncClient(timeout=65) as client:
                resp = await client.post(f"http://127.0.0.1:{api_port}/api/ensure-project")
                result = resp.json()
            if result.get("success"):
                logger.info("[Recovery] ensure_project OK: %s", result.get("data", {}))
                return True
            error = result.get("error", "unknown")
            logger.warning("[Recovery] ensure_project failed: %s", error)
            return False
        except Exception as e:
            logger.warning("[Recovery] ensure_project error: %s", e)
            return False

    async def _check_extension_connected(self, api_port: int) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"http://127.0.0.1:{api_port}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("extension_connected", False)
        except Exception:
            pass
        return False

    async def _wait_extension_connect(self, api_port: int, timeout: float = 30) -> bool:
        """Poll agent health until extension is connected or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self._check_extension_connected(api_port):
                return True
            await asyncio.sleep(3)
        return False

    def get_status(self) -> dict:
        """Get recovery status for all instances."""
        return {
            name: {
                "recovery_level": state.recovery_level,
                "recovering": state.recovering,
                "recovery_count": state.recovery_count,
                "current_ipv6": state.current_ipv6,
                "current_seed": state.current_seed,
                "level_attempts": dict(state.level_attempts),
                "last_recovery_time": state.last_recovery_time,
            }
            for name, state in self.states.items()
        }
