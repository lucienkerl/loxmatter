# loxmatter - connects Matter devices to a Loxone Miniserver.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Stage 2: zigbee-herdsman-converters itself, in quickjs-ng
(design 2026-11-09, section 9).

The bundle `herdsman_data/engine.js` holds the library, zigbee-herdsman's
device model and ZCL codec, and `scripts/herdsman/bridge.js`. This module
runs it and is the only one that knows it is JavaScript: what goes in are
frames and property values, what comes out are frames to send, bindings to
make, records to keep and properties the device reported.

**One thread.** A QuickJS context must only ever be entered from one
thread, so every call runs on a dedicated single-thread executor, and after
each call the engine's pending promise jobs are run until none is left.

**The engine never calls Python.** quickjs-ng refuses a call into Python
while a time limit is set, so what the engine asks of the host - send a
frame, start a timer, keep a record, publish properties, answer a call -
it queues in an outbox, and `_run` collects the outbox after each call and
acts on it here, on the event loop.

**Nothing in here takes the bridge down.** A call that throws, runs past
its time limit or exhausts the memory limit fails that call with
`EngineError`; the caller (`library.py`) decides what that means for the
device. An engine that cannot start at all - no bundle, no quickjs - is
`EngineUnavailableError`, and the library falls back to the catalogue."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loxmatter.zigbee.herdsman.catalogue import ENGINE_FILE

logger = logging.getLogger(__name__)

# Per call into the engine: a converter that loops must not hold the thread.
CALL_TIME_LIMIT_SECONDS = 5
# The whole context. All 4653 definitions evaluated measured +108 MB; a
# context that needs four times that is broken, not busy.
MEMORY_LIMIT_BYTES = 512 * 1024 * 1024

# Sends a frame: (ieee, endpoint, cluster, frame, source endpoint, profile).
Sender = Callable[[str, int, int, bytes, int, int], Awaitable[None]]
# Binds or unbinds: (kind, ieee, endpoint, cluster).
Binder = Callable[[str, str, int, int], Awaitable[None]]


class EngineError(RuntimeError):
    """One call into the engine failed."""


class EngineUnavailableError(EngineError):
    """The engine cannot run here at all."""


@dataclass(frozen=True)
class Attached:
    """What the engine answers when a device is attached."""

    model: str
    vendor: str
    description: str
    exposes: list[dict[str, Any]]
    endpoints: dict[str, int] | None


class Engine:
    """One QuickJS context with the library, shared by every engine device."""

    def __init__(
        self,
        *,
        send: Sender,
        bind: Binder,
        save: Callable[[str, dict[str, Any]], None],
        publish: Callable[[str, dict[str, Any]], None],
        next_tsn: Callable[[], int],
        bundle: Path = ENGINE_FILE,
    ) -> None:
        self._send = send
        self._bind = bind
        self._save = save
        self._publish = publish
        self._next_tsn = next_tsn
        self._bundle = bundle
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._context: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._start_lock = asyncio.Lock()
        self._next_call = 1
        self._calls: dict[int, asyncio.Future[Any]] = {}
        self._timers: dict[int, asyncio.TimerHandle] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def started(self) -> bool:
        return self._context is not None

    # ---------------------------------------------------------- lifecycle --

    async def start(self, coordinator_ieee: str) -> None:
        """Loads the bundle, once. Off the event loop: it takes a second
        here and several on a Pi."""
        async with self._start_lock:
            if self._context is not None:
                return
            if self._closed:
                raise EngineUnavailableError("the engine was closed")
            if not self._bundle.is_file():
                raise EngineUnavailableError(
                    f"{self._bundle} is missing - run scripts/herdsman/build.mjs"
                )
            self._loop = asyncio.get_running_loop()
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="loxmatter-herdsman"
            )
            started = time.monotonic()
            try:
                self._context = await self._loop.run_in_executor(self._executor, self._load)
            except Exception as exc:
                self._executor.shutdown(wait=False)
                self._executor = None
                raise EngineUnavailableError(f"the engine did not start: {exc}") from exc
            logger.info(
                "zigbee2mqtt device library engine loaded in %.1f s", time.monotonic() - started
            )
            await self._run(lambda context: context.get("lx_coordinator")(coordinator_ieee))

    def _load(self) -> Any:
        import quickjs

        context = quickjs.Context()
        context.set_memory_limit(MEMORY_LIMIT_BYTES)
        context.eval(self._bundle.read_text(encoding="utf-8"))
        context.set_time_limit(CALL_TIME_LIMIT_SECONDS)
        return context

    async def close(self) -> None:
        self._closed = True
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()
        for future in self._calls.values():
            if not future.done():
                future.set_exception(EngineError("the engine was closed"))
        self._calls.clear()
        executor, self._executor = self._executor, None
        self._context = None
        if executor is not None:
            executor.shutdown(wait=False)

    # -------------------------------------------------------------- calls --

    async def _run(self, action: Callable[[Any], Any]) -> Any:
        """Runs `action(context)` on the engine thread, then the promise jobs
        it started, then acts on what the engine asked of the host."""
        executor, context, loop = self._executor, self._context, self._loop
        if executor is None or context is None or loop is None:
            raise EngineUnavailableError("the engine is not running")
        tsn = int(self._next_tsn()) & 0xFF

        def work() -> tuple[Any, str, Exception | None]:
            failure: Exception | None = None
            result: Any = None
            try:
                context.get("lx_tsn")(tsn)
                result = action(context)
                while context.execute_pending_job():
                    pass
            except Exception as exc:  # noqa: BLE001 - quickjs raises its own JSException
                failure = exc
            # Collected even after a failure: what the engine queued before
            # it failed - a log line, a timer - still happened.
            try:
                outbox = str(context.get("lx_outbox")())
            except Exception:  # noqa: BLE001 - a context past its limits answers nothing
                outbox = "[]"
            return result, outbox, failure

        result, outbox, failure = await loop.run_in_executor(executor, work)
        for message in json.loads(outbox):
            self._dispatch(message)
        if failure is not None:
            text = str(failure).strip()
            raise EngineError(text.splitlines()[0] if text else repr(failure)) from failure
        return result

    async def _call(self, function: str, *args: Any, timeout: float = 120.0) -> Any:
        """Calls an asynchronous `lx_*` function and waits for `__host_done`."""
        loop = self._loop
        if loop is None:
            raise EngineUnavailableError("the engine is not running")
        call_id = self._next_call
        self._next_call += 1
        future: asyncio.Future[Any] = loop.create_future()
        self._calls[call_id] = future
        try:
            await self._run(lambda context: context.get(function)(call_id, *args))
            return await asyncio.wait_for(future, timeout)
        finally:
            self._calls.pop(call_id, None)

    async def attach(self, record: dict[str, Any], state: dict[str, Any]) -> Attached | None:
        answer = await self._call("lx_attach", json.dumps(record), json.dumps(state))
        if answer is None:
            return None
        endpoints = answer.get("endpoints")
        return Attached(
            model=str(answer.get("model") or ""),
            vendor=str(answer.get("vendor") or ""),
            description=str(answer.get("description") or ""),
            exposes=list(answer.get("exposes") or ()),
            endpoints=(
                {str(k): int(v) for k, v in endpoints.items() if isinstance(v, int)}
                if isinstance(endpoints, dict)
                else None
            ),
        )

    async def detach(self, ieee: str) -> None:
        if self._context is not None:
            await self._run(lambda context: context.get("lx_detach")(ieee))

    async def frame(
        self, ieee: str, endpoint: int, cluster: int, data: bytes, lqi: int, destination: int
    ) -> None:
        await self._call("lx_frame", ieee, endpoint, cluster, data.hex(), lqi, destination)

    async def set(self, ieee: str, key: str, value: Any, endpoint_name: str | None) -> None:
        await self._call("lx_set", ieee, key, json.dumps(value), endpoint_name or "")

    async def get(self, ieee: str, key: str, endpoint_name: str | None) -> bool:
        return bool(await self._call("lx_get", ieee, key, endpoint_name or ""))

    async def configure(self, ieee: str) -> bool:
        return bool(await self._call("lx_configure", ieee, timeout=300.0))

    # ------------------------------------------- what the engine asks of us --

    def _dispatch(self, message: list[Any]) -> None:
        """One entry of the engine's outbox, on the event loop."""
        name, args = message[0], message[1:]
        try:
            handler = self._handlers[name]
        except KeyError:
            logger.debug("unknown engine request %r", name)
            return
        try:
            handler(*args)
        except Exception:
            logger.exception("handling the engine's %s request failed", name)

    @property
    def _handlers(self) -> dict[str, Callable[..., None]]:
        return {
            "log": self._host_log,
            "timer": self._host_timer,
            "clear_timer": self._host_clear_timer,
            "send": self._host_send,
            "bind": self._host_bind,
            "save": lambda ieee, record: self._save(ieee, json.loads(record)),
            "publish": lambda ieee, payload: self._publish(ieee, json.loads(payload)),
            "done": self._host_done,
        }

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _host_log(self, level: str, text: str) -> None:
        # The library logs a great deal at debug and info that only a
        # zigbee2mqtt user reading its own log wants.
        number = {"error": logging.WARNING, "warning": logging.INFO}.get(level, logging.DEBUG)
        logger.log(number, "engine: %s", text)

    def _host_timer(self, timer_id: int, ms: float) -> None:
        old = self._timers.pop(timer_id, None)
        if old is not None:
            old.cancel()
        loop = self._loop
        if loop is None or self._closed:
            return
        self._timers[timer_id] = loop.call_later(max(0.0, float(ms)) / 1000, self._fire, timer_id)

    def _host_clear_timer(self, timer_id: int) -> None:
        handle = self._timers.pop(timer_id, None)
        if handle is not None:
            handle.cancel()

    def _fire(self, timer_id: int) -> None:
        self._timers.pop(timer_id, None)

        async def fire() -> None:
            try:
                await self._run(lambda context: context.get("lx_fire")(timer_id))
            except EngineError as exc:
                logger.info("an engine timer failed: %s", exc)

        self._spawn(fire())

    def _host_send(
        self,
        call_id: int,
        ieee: str,
        endpoint: int,
        cluster: int,
        hex_frame: str,
        source_endpoint: int,
        profile: int,
    ) -> None:
        frame = bytes.fromhex(hex_frame)

        async def send() -> None:
            error = ""
            try:
                await self._send(
                    ieee, int(endpoint), int(cluster), frame, int(source_endpoint), int(profile)
                )
            except Exception as exc:  # noqa: BLE001 - handed to the engine as its error
                error = str(exc) or type(exc).__name__
            with contextlib.suppress(EngineError):
                await self._run(lambda context: context.get("lx_sent")(call_id, error))

        self._spawn(send())

    def _host_bind(self, call_id: int, kind: str, ieee: str, endpoint: int, cluster: int) -> None:
        async def bind() -> None:
            error = ""
            try:
                await self._bind(kind, ieee, int(endpoint), int(cluster))
            except Exception as exc:  # noqa: BLE001 - handed to the engine as its error
                error = str(exc) or type(exc).__name__
            with contextlib.suppress(EngineError):
                await self._run(lambda context: context.get("lx_bound")(call_id, error))

        self._spawn(bind())

    def _host_done(self, call_id: int, value_json: str, error: str) -> None:
        future = self._calls.get(call_id)
        if future is None or future.done():
            return
        if error:
            future.set_exception(EngineError(error))
        else:
            future.set_result(json.loads(value_json))
