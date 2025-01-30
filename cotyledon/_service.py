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

import contextlib
import logging
import os
import random
import signal
import sys
import threading

from cotyledon import _utils


LOG = logging.getLogger(__name__)


class Service:
    """Base class for a service

    This class will be executed in a new child process/worker
    :py:class:`ServiceWorker` of a :py:class:`ServiceManager`. It registers
    signals to manager the reloading and the ending of the process.

    Methods :py:meth:`run`, :py:meth:`terminate` and :py:meth:`reload` are
    optional.
    """

    name = None
    """Service name used in the process title and the log messages in
    additional of the worker_id."""

    graceful_shutdown_timeout = None
    """Timeout after which a gracefully shutdown service will exit. zero means
    endless wait. None means same as ServiceManager that launch the service"""

    def __init__(self, worker_id) -> None:
        """Create a new Service

        :param worker_id: the identifier of this service instance
        :type worker_id: int

        The identifier of the worker can be used for workload repartition
        because it's consistent and always the same.

        For example, if the number of workers for this service is 3,
        one will got 0, the second got 1 and the last got 2.
        if worker_id 1 died, the new spawned process will got 1 again.
        """
        super().__init__()
        self._initialize(worker_id)

    def _initialize(self, worker_id) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        if self.name is None:
            self.name = self.__class__.__name__
        self.worker_id = worker_id
        self.pid = os.getpid()

        self._signal_lock = threading.Lock()

        # Only used by oslo_config_glue for now, so we don't need
        # to have a list of hook
        self._on_reload_internal_hook = self._noop_hook

    def _noop_hook(self, service) -> None:
        pass

    def terminate(self) -> None:
        """Gracefully shutdown the service

        This method will be executed when the Service has to shutdown cleanly.

        If not implemented the process will just end with status 0.

        To customize the exit code, the :py:class:`SystemExit` exception can be
        used.

        Any exceptions raised by this method will be logged and the worker will
        exit with status 1.
        """

    def reload(self) -> None:  # noqa: PLR6301
        """Reloading of the service

        This method will be executed when the Service receives a SIGHUP.

        If not implemented the process will just end with status 0 and
        :py:class:`ServiceRunner` will start a new fresh process for this
        service with the same worker_id.

        Any exceptions raised by this method will be logged and the worker will
        exit with status 1.
        """
        os.kill(os.getpid(), signal.SIGTERM)

    def run(self) -> None:
        """Method representing the service activity

        If not implemented the process will just wait to receive an ending
        signal.

        This method is ran into the thread and can block or return as needed

        Any exceptions raised by this method will be logged and the worker will
        exit with status 1.
        """

    # Helper to run application methods in a safety way when signal are
    # received

    def _reload(self) -> None:
        with _utils.exit_on_exception():
            if self._signal_lock.acquire(blocking=False):
                try:
                    self._on_reload_internal_hook(self)
                    self.reload()
                finally:
                    self._signal_lock.release()

    def _terminate(self) -> None:
        with _utils.exit_on_exception(), self._signal_lock:
            self.terminate()
            sys.exit(0)

    def _run(self) -> None:
        with _utils.exit_on_exception():
            self.run()


class ServiceConfig:
    def __init__(self, service_id, service, workers, args, kwargs) -> None:
        self.service = service
        self.workers = workers
        self.args = args
        self.kwargs = kwargs
        self.service_id = service_id


class ServiceWorker(_utils.SignalManager):
    """Service Worker Wrapper

    This represents the child process spawned by ServiceManager

    All methods implemented here, must run in the main threads
    """

    @classmethod
    def create_and_wait(cls, *args, **kwargs) -> None:
        sw = cls(*args, **kwargs)
        sw.wait_forever()

    def __init__(  # noqa: PLR0917, PLR0913
        self,
        config,
        service_id,
        worker_id,
        parent_pipe,
        started_hooks,
        graceful_shutdown_timeout,
    ) -> None:
        super().__init__()
        self._ready = threading.Event()
        _utils.spawn(self._watch_parent_process, parent_pipe)

        # Reseed random number generator
        random.seed()

        args = () if config.args is None else config.args
        kwargs = {} if config.kwargs is None else config.kwargs
        self.service = config.service(worker_id, *args, **kwargs)
        self.service._initialize(worker_id)  # noqa: SLF001
        if self.service.graceful_shutdown_timeout is None:
            self.service.graceful_shutdown_timeout = graceful_shutdown_timeout

        self.title = f"{self.service.name}({worker_id}) [{os.getpid()}]"

        # Set process title
        _utils.setproctitle(
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

    def _watch_parent_process(self, parent_pipe) -> None:
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
                self._signals_received.appendleft(signal.SIGTERM)
        else:
            os._exit(0)

    def _alarm(self) -> None:
        LOG.info(
            "Graceful shutdown timeout (%d) exceeded, exiting %s now.",
            self.service.graceful_shutdown_timeout,
            self.title,
        )
        os._exit(1)

    def _fast_exit(self) -> None:
        LOG.info(
            "Caught SIGINT signal, instantaneous exiting of service %s",
            self.title,
        )
        os._exit(1)

    def _on_signal_received(self, sig) -> None:
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

            if self.service.graceful_shutdown_timeout > 0:
                if os.name == "posix":
                    signal.alarm(self.service.graceful_shutdown_timeout)
                else:
                    threading.Timer(
                        self.service.graceful_shutdown_timeout,
                        self._alarm,
                    ).start()
            _utils.spawn(self.service._terminate)  # noqa: SLF001
        elif sig == _utils.SIGHUP:
            _utils.spawn(self.service._reload)  # noqa: SLF001

    def wait_forever(self) -> None:
        LOG.debug("Run service %s", self.title)
        _utils.spawn(self.service._run)  # noqa: SLF001
        super()._wait_forever()
