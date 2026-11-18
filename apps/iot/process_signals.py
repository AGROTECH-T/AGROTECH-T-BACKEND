"""Graceful SIGTERM/SIGINT handling for long-running IoT processes (VALIDATION-02).

Without a handler, Python running as PID 1 in a container ignores SIGTERM
(the kernel drops default-action signals for PID 1), so the platform waits for
its grace period and then sends SIGKILL. Installing a handler turns SIGTERM
into a cooperative stop request:

- the MQTT consumer stops accepting messages, never ACKs after the request and
  leaves anything unacknowledged to the broker's persistent session;
- the delivery worker finishes the current bounded batch and exits; rows it
  could not finish keep their lease and are recovered after expiry.

The handler only calls the supplied ``stop`` callable; it does no I/O itself
apart from logging.
"""

from __future__ import annotations

import logging
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

STOP_SIGNALS = tuple(
    sig for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)) if sig
)


@contextmanager
def graceful_stop(stop: Callable[[], None], *, process: str) -> Iterator[list[int]]:
    """Install handlers for the block; yields the list of received signals."""
    received: list[int] = []

    def handler(signum, _frame):
        received.append(signum)
        logger.info("%s: señal %s recibida; deteniendo de forma ordenada", process, signum)
        stop()

    previous = {}
    for sig in STOP_SIGNALS:
        previous[sig] = signal.signal(sig, handler)
    try:
        yield received
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)


__all__ = ["STOP_SIGNALS", "graceful_stop"]
