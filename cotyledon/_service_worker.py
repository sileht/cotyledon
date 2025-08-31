# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import contextlib
import logging
import os
import random
import signal
import sys
import threading
import typing

from cotyledon import _service
from cotyledon import _utils
from cotyledon import types as t


if typing.TYPE_CHECKING:
    import multiprocessing.connection

try:
    # Py 3.12+
    from typing import Unpack  # type: ignore[attr-defined]
except ImportError:
    # Py 3.10-3.11
    from typing_extensions import Unpack


LOG = logging.getLogger(__name__)

MultiprocessingPipe: typing.TypeAlias = tuple[
    "multiprocessing.connection.Connection",
    "multiprocessing.connection.Connection",
]


OnNewWorkerHook: typing.TypeAlias = typing.Callable[
    [t.ServiceId, t.WorkerId, _service.Service],
    None,
]

P = typing.ParamSpec("P")
R = typing.TypeVar("R")

ServiceT = typing.TypeVar("ServiceT", bound=_service.Service)
ServiceArgsT: typing.TypeAlias = tuple[Unpack[P.args]]
ServiceKwArgsT: typing.TypeAlias = dict[str, Unpack[P.kwargs]]


class ServiceConfig(typing.Generic[ServiceT, P]):
    def __init__(
        self,
        service_id: t.ServiceId,
        service: type[ServiceT],
        workers: int,
        args: ServiceArgsT | None,
        kwargs: ServiceKwArgsT | None,
    ) -> None:
        self.service = service
        self.workers = workers
        self.args = () if args is None else args
        self.kwargs = {} if kwargs is None else kwargs
        self.service_id = service_id

    def build(self, worker_id: t.WorkerId) -> ServiceT:
        return self.service(worker_id, *self.args, **self.kwargs)


class ServiceWorker(_utils.SignalManager):
    """Service Worker Wrapper

    This represents the child process spawned by ServiceManager

    All methods implemented here, must run in the main threads
    """

    @classmethod
    def create_and_wait(  # noqa: PLR0913,PLR0917
        cls,
        started_event: multiprocessing.synchronize.Event,
        config: ServiceConfig[typing.Any, typing.Any],
        service_id: t.ServiceId,
        worker_id: t.WorkerId,
        parent_pipe: MultiprocessingPipe,
        started_hooks: list[OnNewWorkerHook],
        graceful_shutdown_timeout: int,
    ) -> None:
        sw = cls(
            config,
            service_id,
            worker_id,
            parent_pipe,
            started_hooks,
            graceful_shutdown_timeout,
        )
        started_event.set()
        sw.wait_forever()

    def __init__(  # noqa: PLR0917, PLR0913
        self,
        config: ServiceConfig[typing.Any, typing.Any],
        service_id: t.ServiceId,
        worker_id: t.WorkerId,
        parent_pipe: MultiprocessingPipe,
        started_hooks: list[OnNewWorkerHook],
        graceful_shutdown_timeout: int,
    ) -> None:
        super().__init__()
        self._ready = threading.Event()
        self._signal_lock = threading.Lock()

        _utils.spawn(self._watch_parent_process, parent_pipe)

        # Reseed random number generator
        random.seed()

        self.service: _service.Service = config.build(worker_id)
        # Backward compat: Just in case the __init__ is not called on the service
        self.service.worker_id = worker_id

        self.title = f"{self.service.name}({worker_id}) [{os.getpid()}]"
        self._graceful_shutdown_timeout = graceful_shutdown_timeout

        # Set process title
        _utils.set_process_title(
            f"{_utils.get_process_name()}: {self.service.name} worker({worker_id})",
        )

        # We are ready tell them
        self._ready.set()
        _utils.run_hooks(
            "new_worker",
            started_hooks,
            service_id,
            worker_id,
            self.service,
        )

    def _get_graceful_shutdown_timeout(self) -> int:
        if self.service.graceful_shutdown_timeout is None:
            return self._graceful_shutdown_timeout

        return self.service.graceful_shutdown_timeout

    def _watch_parent_process(self, parent_pipe: MultiprocessingPipe) -> None:
        # This will block until the write end is closed when the parent
        # dies unexpectedly
        parent_pipe[1].close()
        with contextlib.suppress(EOFError):
            parent_pipe[0].recv()

        if self._ready.is_set():
            LOG.info("Parent process has died unexpectedly, %s exiting", self.title)
            if os.name == "posix":
                os.kill(os.getpid(), signal.SIGTERM)
            else:
                # Fallback to process signal later
                self._on_signal_received(signal.SIGTERM)
        else:
            os._exit(0)

    def _alarm(self) -> None:
        LOG.info(
            "Graceful shutdown timeout (%d) exceeded, exiting %s now.",
            self._get_graceful_shutdown_timeout(),
            self.title,
        )
        os._exit(1)

    def _fast_exit(self) -> None:
        LOG.info(
            "Caught SIGINT signal, instantaneous exiting of service %s",
            self.title,
        )
        os._exit(1)

    def _on_signal_received(self, sig: int) -> None:
        # Code below must not block to return to select.select() and catch
        # next signals
        if sig == _utils.SIGALRM:
            self._alarm()
        if sig == signal.SIGINT:
            self._fast_exit()
        elif sig == signal.SIGTERM:
            LOG.info(
                "Caught SIGTERM signal, graceful exiting of service %s",
                self.title,
            )

            timeout = self._get_graceful_shutdown_timeout()
            if timeout > 0:
                if os.name == "posix":
                    signal.alarm(timeout)
                else:
                    threading.Timer(timeout, self._alarm).start()
            _utils.spawn(self._service_terminate)
        elif sig == _utils.SIGHUP:
            _utils.spawn(self._service_reload)

    def _service_run(self) -> None:
        with _utils.exit_on_exception():
            self.service.run()

    def _service_terminate(self) -> None:
        with _utils.exit_on_exception(), self._signal_lock:
            self.service.terminate()
            sys.exit(0)

    def _service_reload(self) -> None:
        with _utils.exit_on_exception():
            if self._signal_lock.acquire(blocking=False):
                try:
                    self.service.reload()
                finally:
                    self._signal_lock.release()

    def wait_forever(self) -> None:
        LOG.debug("Run service %s", self.title)
        _utils.spawn(self._service_run)
        super()._wait_forever()
