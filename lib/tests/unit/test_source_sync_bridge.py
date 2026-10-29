"""Sync compatibility uses one async driver and owns its worker resources."""

from __future__ import annotations

import asyncio
import contextvars
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

import blue_data_llm_source.source as source_module
from blue_data_llm_source import Budget, Limits, LlmSource, LlmSourceError
from blue_data_llm_source.llm.client import DefaultLlmClient
from tests.doubles import LitellmStub, ScriptedClient, chat_payload, install_litellm
from tests.unit.test_source_limit_composition import invoke

ROWS = '[{"item":"alpha"}]'
ROUTES = ["fetch", "native", "nl", "tool", "registry", "registry_nl"]


@pytest.mark.parametrize("route", ROUTES)
def test_all_sync_routes_in_running_loop_use_async_provider(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    marker = contextvars.ContextVar("request_marker", default="absent")
    caller_thread = threading.get_ident()
    worker_loops: list[asyncio.AbstractEventLoop] = []
    worker_threads: list[int] = []
    stub = LitellmStub(chat_payload(ROWS))

    async def answer(**kwargs: Any) -> Any:
        assert marker.get() == "request"
        worker_threads.append(threading.get_ident())
        worker_loops.append(asyncio.get_running_loop())
        return stub.completion(**kwargs)

    def forbidden(**_kwargs: Any) -> Any:
        raise AssertionError("sync provider must never drive source execution")

    module = stub.as_module()
    module.acompletion = answer  # type: ignore[attr-defined]
    module.completion = forbidden  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "litellm", module)
    source = LlmSource()

    async def run() -> None:
        caller_loop = asyncio.get_running_loop()
        marker.set("request")
        for _ in range(3):
            assert invoke(source, route, {}) == [{"item": "alpha"}]
        assert asyncio.get_running_loop() is caller_loop
        assert not caller_loop.is_closed()

    asyncio.run(run())
    assert len(worker_threads) == 3
    assert all(ident != caller_thread for ident in worker_threads)
    assert all(loop.is_closed() for loop in worker_loops)
    with DefaultLlmClient._semaphore_lock:  # pyright: ignore[reportPrivateUsage]
        assert all(loop not in DefaultLlmClient._semaphores for loop in worker_loops)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("failure", [False, True])
def test_result_error_parity_and_worker_cleanup(
    monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    stub = install_litellm(monkeypatch, LitellmStub(chat_payload("invalid" if failure else ROWS)))
    source = LlmSource()
    loop_factory = asyncio.new_event_loop
    loops: list[asyncio.AbstractEventLoop] = []

    def new_loop() -> asyncio.AbstractEventLoop:
        loop = loop_factory()
        loops.append(loop)
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", new_loop)
    before_threads = set(threading.enumerate())

    async def run() -> None:
        for _ in range(4):
            if failure:
                with pytest.raises(LlmSourceError) as sync_error:
                    source.fetch("items")
                with pytest.raises(type(sync_error.value)) as async_error:
                    await source.afetch("items")
                assert str(async_error.value) == str(sync_error.value)
            else:
                assert source.fetch("items").records == (await source.afetch("items")).records

    asyncio.run(run())
    assert len(stub.calls) == 8
    assert len(loops) == 4
    assert all(loop.is_closed() for loop in loops)
    assert set(threading.enumerate()) == before_threads
    with DefaultLlmClient._semaphore_lock:  # pyright: ignore[reportPrivateUsage]
        assert all(loop not in DefaultLlmClient._semaphores for loop in loops)  # pyright: ignore[reportPrivateUsage]


def test_same_parent_budget_and_context_cross_the_worker() -> None:
    parent = Budget(Limits(max_calls=1, max_cost=1))
    source = LlmSource(client=ScriptedClient([ROWS], cost=0.25))

    async def run() -> None:
        assert source.fetch("items", budget=parent).records
        assert source.fetch("items", budget=parent).records == []

    asyncio.run(run())
    assert parent.calls == 1
    assert parent.cost == 0.25


def test_concurrent_workers_share_the_parent_ledger_without_deadlock() -> None:
    barrier = threading.Barrier(2, timeout=5)
    parent = Budget(Limits(max_calls=2))
    loops: list[asyncio.AbstractEventLoop] = []
    lock = threading.Lock()

    class Client(ScriptedClient):
        async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            with lock:
                loops.append(asyncio.get_running_loop())
            barrier.wait()
            return await super().acomplete(prompt, properties)

    def caller() -> list[dict[str, Any]]:
        async def run() -> list[dict[str, Any]]:
            return LlmSource(client=Client([ROWS])).fetch("items", budget=parent).records

        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(caller) for _ in range(2)]
        assert all(future.result(timeout=10) == [{"item": "alpha"}] for future in futures)
    assert parent.calls == 2
    assert len(set(loops)) == 2
    assert all(loop.is_closed() for loop in loops)


def test_loop_affine_client_fails_promptly_and_afetch_works_on_owner() -> None:
    async def run() -> None:
        owner = asyncio.get_running_loop()

        class Client(ScriptedClient):
            async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
                if asyncio.get_running_loop() is not owner:
                    raise RuntimeError("client belongs to another event loop")
                return await super().acomplete(prompt, properties)

        source = LlmSource(client=Client([ROWS]))
        with pytest.raises(LlmSourceError, match="another event loop"):
            source.fetch("items")
        assert (await source.afetch("items")).records == [{"item": "alpha"}]

    asyncio.run(run())


@pytest.mark.parametrize("ignore_cancellation", [False, True])
def test_cooperative_deadline_and_cancellation_cleanup(
    monkeypatch: pytest.MonkeyPatch, ignore_cancellation: bool
) -> None:
    clock = [0.0]
    parent = Budget(Limits(max_seconds=1), clock=lambda: clock[0])
    cancelled: list[bool] = []
    finished: list[bool] = []
    stub = LitellmStub(chat_payload(ROWS))
    module = stub.as_module()

    async def provider(**_kwargs: Any) -> Any:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append(True)
            if not ignore_cancellation:
                raise
            # Controlled release after cancellation, with no unbounded external waiter.
            await asyncio.sleep(0)
            return stub.payload
        finally:
            finished.append(True)

    async def expire(awaitable: Any, timeout: float) -> Any:
        assert timeout > 0
        task = asyncio.create_task(awaitable)
        # Let the real client enter the provider, then expire its injected budget clock.
        while not stub.entered:
            await asyncio.sleep(0)
        clock[0] = 2.0
        task.cancel()
        try:
            return await task
        except asyncio.CancelledError:
            raise TimeoutError from None

    async def entered(**kwargs: Any) -> Any:
        stub.entered += 1
        return await provider(**kwargs)

    module.acompletion = entered  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "litellm", module)
    monkeypatch.setattr(asyncio, "wait_for", expire)

    async def run() -> None:
        result = LlmSource().fetch("items", budget=parent)
        assert result.meta.stopped_by == "max_seconds"
        assert result.records == []

    asyncio.run(run())
    assert cancelled == finished == [True]
    assert parent.calls == 1


def test_owned_loop_cancels_orphan_tasks_and_closes_generators() -> None:
    cleaned: list[str] = []

    class Client(ScriptedClient):
        async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
            async def background() -> None:
                try:
                    await asyncio.Future()
                finally:
                    cleaned.append("task")

            async def values() -> AsyncIterator[int]:
                try:
                    yield 1
                finally:
                    cleaned.append("generator")

            asyncio.create_task(background())
            gen = values()
            await anext(gen)
            await asyncio.sleep(0)
            return await super().acomplete(prompt, properties)

    async def run() -> None:
        assert LlmSource(client=Client([ROWS])).fetch("items").records

    asyncio.run(run())
    assert sorted(cleaned) == ["generator", "task"]


async def test_caller_cancellation_callback_waits_for_sync_return() -> None:
    task = asyncio.current_task()
    assert task is not None
    callbacks: list[bool] = []

    def cancel() -> None:
        callbacks.append(True)
        task.cancel()

    asyncio.get_running_loop().call_soon(cancel)
    assert LlmSource(client=ScriptedClient([ROWS])).fetch("items").records
    assert callbacks == []
    with pytest.raises(asyncio.CancelledError):
        await asyncio.sleep(0)
    assert callbacks == [True]


def _interrupt_the_wait(
    monkeypatch: pytest.MonkeyPatch, ready: threading.Event | None
) -> list[float]:
    """Raise KeyboardInterrupt in the waiting thread, as Ctrl-C does on Python 3.10.

    With *ready*, the interrupt lands once the provider call is in flight. Without it, the
    interrupt lands first and the worker starts only once the caller waits out the grace.
    """
    raised: list[float] = []
    grace = threading.Event()
    real_wait = getattr(source_module, "wait")

    def wait(fs: Any, timeout: float | None = None) -> Any:
        if not raised:
            if ready is not None:
                assert ready.wait(5)
            raised.append(time.monotonic())
            raise KeyboardInterrupt
        grace.set()
        return real_wait(fs, timeout=timeout)

    class Executor(ThreadPoolExecutor):
        def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
            def gated(*args: Any, **kwargs: Any) -> Any:
                if ready is None:
                    assert grace.wait(5)
                return fn(*args, **kwargs)

            return super().submit(gated, *args, **kwargs)

    monkeypatch.setattr(source_module, "ThreadPoolExecutor", Executor)
    monkeypatch.setattr(source_module, "wait", wait)
    return raised


class _SlowClient(ScriptedClient):
    """An async client whose provider call takes *delay* seconds and records how it ended."""

    def __init__(self, delay: float, ignore_cancellation: bool = False) -> None:
        super().__init__([ROWS])
        self.delay = delay
        self.ignore_cancellation = ignore_cancellation
        self.entered = threading.Event()
        self.ended = threading.Event()
        self.log: list[str] = []

    async def acomplete(self, prompt: str, properties: dict[str, Any] | None = None) -> str:
        self.entered.set()
        try:
            try:
                await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                self.log.append("cancelled")
                if not self.ignore_cancellation:
                    raise
                await asyncio.sleep(self.delay)
            self.log.append("completed")
            return await super().acomplete(prompt, properties)
        finally:
            self.ended.set()


@pytest.mark.parametrize("route", ["fetch", "native", "nl"])
def test_interrupt_in_running_loop_cancels_the_owned_fetch(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    client = _SlowClient(delay=3)
    raised = _interrupt_the_wait(monkeypatch, client.entered)
    before_threads = set(threading.enumerate())

    async def run() -> None:
        with pytest.raises(KeyboardInterrupt):
            invoke(LlmSource(client=client), route, {})

    asyncio.run(run())
    assert time.monotonic() - raised[0] < 1
    assert client.log == ["cancelled"]
    assert set(threading.enumerate()) == before_threads


def test_interrupt_before_the_task_exists_never_contacts_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _SlowClient(delay=3)
    raised = _interrupt_the_wait(monkeypatch, None)

    async def run() -> None:
        with pytest.raises(KeyboardInterrupt):
            LlmSource(client=client).fetch("items")

    asyncio.run(run())
    assert time.monotonic() - raised[0] < 1
    assert not client.entered.is_set()
    assert client.prompts == []


def test_interrupt_waits_only_a_bounded_time_for_a_stubborn_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _SlowClient(delay=1, ignore_cancellation=True)
    raised = _interrupt_the_wait(monkeypatch, client.entered)
    monkeypatch.setattr(source_module, "_INTERRUPT_GRACE_SECONDS", 0.1)
    before_threads = set(threading.enumerate())

    async def run() -> None:
        with pytest.raises(KeyboardInterrupt):
            LlmSource(client=client).fetch("items")

    asyncio.run(run())
    assert time.monotonic() - raised[0] < 0.8
    assert not client.ended.is_set()
    # The worker is left to finish on its own.
    for thread in set(threading.enumerate()) - before_threads:
        thread.join(5)
    assert client.log == ["cancelled", "completed"]


@pytest.mark.parametrize("hold_worker", [True, False])
def test_interrupt_inside_submit_leaves_no_fetch_running(
    monkeypatch: pytest.MonkeyPatch, hold_worker: bool
) -> None:
    client = _SlowClient(delay=3)
    release = threading.Event()

    class Executor(ThreadPoolExecutor):
        def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
            def held(*args: Any, **kwargs: Any) -> Any:
                if hold_worker:
                    assert release.wait(5)
                return fn(*args, **kwargs)

            # The work is queued and a thread started, but the future never comes back.
            super().submit(held, *args, **kwargs)
            raise KeyboardInterrupt

    monkeypatch.setattr(source_module, "ThreadPoolExecutor", Executor)
    before_threads = set(threading.enumerate())

    async def run() -> None:
        with pytest.raises(KeyboardInterrupt):
            LlmSource(client=client).fetch("items")

    started = time.monotonic()
    asyncio.run(run())
    assert time.monotonic() - started < 1
    release.set()
    for thread in set(threading.enumerate()) - before_threads:
        thread.join(5)
        assert not thread.is_alive()
    assert "completed" not in client.log
    if hold_worker:
        assert not client.entered.is_set()


@pytest.mark.skipif(sys.platform == "win32", reason="sends a POSIX SIGINT to the main thread")
def test_first_ctrl_c_under_asyncio_run_cancels_the_fetch() -> None:
    # A real signal through asyncio.run's own handler, which on the first Ctrl-C only
    # requests cancellation of the main task. In-process, since the suite refuses to start
    # subprocesses; the provider would otherwise wait out twice the grace period.
    assert threading.current_thread() is threading.main_thread()
    client = _SlowClient(delay=10)
    signalled: list[float] = []

    def ctrl_c() -> None:
        if client.entered.wait(5):
            signalled.append(time.monotonic())
            signal.pthread_kill(threading.main_thread().ident or 0, signal.SIGINT)

    async def main() -> None:
        LlmSource(client=client).fetch("items")

    # asyncio.run installs its handler only over the default one, which a runner started
    # in the background may have replaced with "ignore".
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    sender = threading.Thread(target=ctrl_c)
    try:
        sender.start()
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(main())
    finally:
        sender.join()
        signal.signal(signal.SIGINT, previous)
    assert len(signalled) == 1
    assert time.monotonic() - signalled[0] < 1
    assert client.log == ["cancelled"]
