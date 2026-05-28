"""
FlowKit Recovery Manager — autonomous 403 recovery with escalation levels.

Levels:
  0: Gateway rotates to another instance (handled by gateway.py)
  1: Extension reset captcha (clear cookies/cache, reload, re-capture token)
  2: Rotate IPv6 + restart Chrome with new proxy
  3: Kill Chrome + new fingerprint + restart Chrome (+ optional new IPv6)

Each instance tracks its own recovery state independently.
Recovery is triggered when an instance enters cooldown (consecutive 403 >= threshold).
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
        self._recovery_tasks[instance_name] = task

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

            if level == 1:
                if state.level_attempts[1] < self.level1_max:
                    state.level_attempts[1] += 1
                    success = await self._level1_reset_captcha(instance_name)
                else:
                    logger.info("[Recovery] %s Level 1 exhausted (%d attempts), escalating",
                                instance_name, state.level_attempts[1])

            if level == 2 or (level == 1 and not success):
                if level == 1:
                    state.recovery_level = 2
                if state.level_attempts[2] < self.level2_max:
                    state.level_attempts[2] += 1
                    success = await self._level2_rotate_ipv6(instance_name)
                else:
                    logger.info("[Recovery] %s Level 2 exhausted (%d attempts), escalating",
                                instance_name, state.level_attempts[2])

            if level == 3 or (not success and state.recovery_level <= 3):
                state.recovery_level = 3
                if state.level_attempts[3] < self.level3_max:
                    state.level_attempts[3] += 1
                    success = await self._level3_restart_chrome(instance_name)
                else:
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
                logger.info("[Recovery] %s Level 1: captcha reset OK, waiting for re-capture...", instance_name)
                await asyncio.sleep(15)
                connected = await self._check_extension_connected(api_port)
                if connected:
                    logger.info("[Recovery] %s Level 1: extension reconnected", instance_name)
                    return True
                else:
                    logger.warning("[Recovery] %s Level 1: extension not connected after reset", instance_name)
                    return False
            else:
                logger.warning("[Recovery] %s Level 1: reset failed: %s",
                               instance_name, result.get("error", "unknown"))
                return False
        except Exception as e:
            logger.warning("[Recovery] %s Level 1 error: %s", instance_name, e)
            return False

    async def _level2_rotate_ipv6(self, instance_name: str) -> bool:
        """Level 2: Rotate IPv6 and restart Chrome with new proxy."""
        logger.info("[Recovery] %s Level 2: rotating IPv6", instance_name)

        if not self._ipv6_client:
            logger.info("[Recovery] %s Level 2: no IPv6 pool, skipping to Level 3", instance_name)
            return False

        state = self.states[instance_name]
        old_ip = state.current_ipv6

        try:
            if old_ip:
                result = self._ipv6_client.rotate_ip(old_ip, reason="403_recovery", worker=instance_name)
            else:
                result = self._ipv6_client.get_ip(worker=instance_name)

            if not result:
                logger.warning("[Recovery] %s Level 2: IPv6 pool returned no IP", instance_name)
                return False

            new_ip = result["ip"]
            state.current_ipv6 = new_ip
            logger.info("[Recovery] %s Level 2: got new IPv6 %s", instance_name, new_ip)

            return await self._restart_chrome_instance(instance_name, new_ipv6=new_ip)
        except Exception as e:
            logger.warning("[Recovery] %s Level 2 error: %s", instance_name, e)
            return False

    async def _level3_restart_chrome(self, instance_name: str) -> bool:
        """Level 3: Kill Chrome, generate new fingerprint, restart."""
        state = self.states[instance_name]
        new_ipv6 = state.current_ipv6 or ""
        logger.info("[Recovery] %s Level 3: full Chrome restart (fingerprint + %s)",
                    instance_name, f"IPv6={new_ipv6}" if new_ipv6 else "no IPv6")

        return await self._restart_chrome_instance(instance_name, new_ipv6=new_ipv6)

    async def _restart_chrome_instance(self, instance_name: str, new_ipv6: str = "") -> bool:
        """Restart Chrome with new fingerprint (and optionally new IPv6)."""
        cfg = self.instances_config.get(instance_name)
        if not cfg:
            return False

        try:
            from launcher import restart_chrome as _restart_chrome
            loop = asyncio.get_running_loop()
            proc = await loop.run_in_executor(
                None,
                lambda: _restart_chrome(cfg, new_ipv6=new_ipv6),
            )

            if not proc:
                logger.warning("[Recovery] %s: Chrome restart returned no process", instance_name)
                return False

            logger.info("[Recovery] %s: Chrome restarted (PID %d), waiting for extension...",
                        instance_name, proc.pid)

            await asyncio.sleep(self.restart_delay)

            api_port = cfg["api_port"]
            connected = await self._wait_extension_connect(api_port, timeout=self.reconnect_timeout)

            if connected:
                from launcher import get_instance_seed
                seed = get_instance_seed(instance_name)
                self.states[instance_name].current_seed = seed
                logger.info("[Recovery] %s: extension reconnected, new seed=%d", instance_name, seed)
                return True
            else:
                logger.warning("[Recovery] %s: extension did not reconnect within %ds",
                               instance_name, self.reconnect_timeout)
                return False

        except Exception as e:
            logger.exception("[Recovery] %s Chrome restart error: %s", instance_name, e)
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
