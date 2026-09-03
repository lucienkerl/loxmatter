"""Tests fuer den zweiten, TLS-gesicherten Listener (Entwurf 4.1/4.2).

Der wichtigste Test hier ist `test_cancelling_asks_every_server_to_stop`.
Er haelt einen am installierten uvicorn-Quelltext abgelesenen Fallstrick
fest: `Server.serve()` setzt seine Signal-Handler mit `signal.signal(...)`
(`uvicorn/server.py`, `capture_signals`), sodass bei zwei Servern im selben
Prozess der zweite den Handler des ersten ueberschreibt - ein Strg-C
beendete dann nur einen, der andere liefe weiter, und der Dienst liesse
sich nicht mehr beenden. Die Neutralisierung, die das aufloest, ist eine
einzige Zuweisung; ohne diesen Test kann sie bei einem uvicorn-Update
stillschweigend wirkungslos werden.

Die Server werden hier durch Doubles ersetzt, nicht wirklich gebunden: was
geprueft wird, ist die Abbruchmechanik, nicht dass uvicorn Sockets oeffnen
kann.
"""

from __future__ import annotations

import asyncio
import signal

import pytest
import uvicorn
from fastapi import FastAPI

from loxmatter.cli import _neutralize_signal_capture, _serve_forever, _server_configs
from loxmatter.tls import TlsState, prepare_tls


class FakeServer:
    """Verhaelt sich wie `uvicorn.Server`, soweit `_serve_forever` es sieht."""

    def __init__(self) -> None:
        self.should_exit = False
        self.started = False
        self.finished = False

    async def serve(self) -> None:
        self.started = True
        while not self.should_exit:
            await asyncio.sleep(0.01)
        self.finished = True


def test_without_tls_there_is_exactly_one_config():
    configs = _server_configs(
        FastAPI(), "0.0.0.0", 8080, TlsState(material=None, port=None, error=None)
    )

    assert len(configs) == 1
    assert configs[0].port == 8080
    assert configs[0].is_ssl is False


def test_with_tls_there_is_a_second_config_on_the_https_port(tmp_path):
    state = prepare_tls(tmp_path / "tls", 8443)

    configs = _server_configs(FastAPI(), "0.0.0.0", 8080, state)

    assert [config.port for config in configs] == [8080, 8443]
    assert configs[0].is_ssl is False
    assert configs[1].is_ssl is True
    assert configs[1].ssl_certfile == str(state.material.certificate)
    assert configs[1].ssl_keyfile == str(state.material.private_key)


def test_both_configs_serve_the_very_same_app(tmp_path):
    """Eine App, zwei Bindungen - kein zweiter Router, kein zweiter Zustand."""
    app = FastAPI()
    state = prepare_tls(tmp_path / "tls", 8443)

    configs = _server_configs(app, "0.0.0.0", 8080, state)

    assert configs[0].app is app
    assert configs[1].app is app


def test_a_neutralized_server_does_not_touch_the_signal_handlers():
    """Der Fallstrick aus Entwurf 4.2, an seiner Wurzel geprueft."""
    server = uvicorn.Server(uvicorn.Config(FastAPI()))
    before = signal.getsignal(signal.SIGINT)

    _neutralize_signal_capture(server)
    with server.capture_signals():
        during = signal.getsignal(signal.SIGINT)

    assert during is before
    assert signal.getsignal(signal.SIGINT) is before


async def test_cancelling_asks_every_server_to_stop():
    """Ohne das haenge der Dienst: der zweite Server wuerde beendet, der
    erste liefe weiter, und niemand saehe warum."""
    servers = [FakeServer(), FakeServer()]

    task = asyncio.create_task(_serve_forever(servers))  # type: ignore[arg-type]
    while not all(server.started for server in servers):
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert all(server.should_exit for server in servers)
    assert all(server.finished for server in servers)


async def test_a_single_server_still_works():
    servers = [FakeServer()]

    task = asyncio.create_task(_serve_forever(servers))  # type: ignore[arg-type]
    while not servers[0].started:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert servers[0].finished
