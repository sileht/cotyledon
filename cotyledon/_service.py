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

import logging
import os
import signal
import sys
import threading

import setproctitle


from cotyledon import _utils

LOG = logging.getLogger(__name__)


class Service(object):
    """Base class for a service

    This class will be executed in a new child process/worker
    :py:class:`ServiceWorker` of a :py:class:`ServiceManager`. It registers
    signals to manager the reloading and the ending of the process.

    Methods :py:meth:`run`, :py:meth:`terminate` and :py:meth:`reload` are
    optional.
    """

    name = None
    """Service name used in the process title and the log messages in additionnal
    of the worker_id."""

    graceful_shutdown_timeout = None
    """Timeout after which a gracefully shutdown service will exit. zero means
    endless wait. None means same as ServiceManager that launch the service"""

    def __init__(self, worker_id):
        """Create a new Service

        :param worker_id: the identifier of this service instance
        :type worker_id: int
        """
        super(Service, self).__init__()
        self._initialize(worker_id)

    def _initialize(self, worker_id):
        if getattr(self, '_initialized', False):
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

    def _noop_hook(self, service):
        pass

    def terminate(self):
        """Gracefully shutdown the service

        This method will be executed when the Service has to shutdown cleanly.

        If not implemented the process will just end with status 0.

        To customize the exit code, the :py:class:`SystemExit` exception can be
        used.

        Any exceptions raised by this method will be logged and the worker will
        exit with status 1.
        """

    def reload(self):
        """Reloading of the service

        This method will be executed when the Service receives a SIGHUP.

        If not implemented the process will just end with status 0 and
        :py:class:`ServiceRunner` will start a new fresh process for this
        service with the same worker_id.

        Any exceptions raised by this method will be logged and the worker will
        exit with status 1.
        """
        os.kill(os.getpid(), signal.SIGTERM)

    def run(self):
        """Method representing the service activity

        If not implemented the process will just wait to receive an ending
        signal.

        This method is ran into the thread and can block or return as needed

        Any exceptions raised by this method will be logged and the worker will
        exit with status 1.
        """

    # Helper to run application methods in a safety way when signal are
    # received

    def _reload(self):
        with _utils.exit_on_exception():
            if self._signal_lock.acquire(False):
                try:
                    self._on_reload_internal_hook(self)
                    self.reload()
                finally:
                    self._signal_lock.release()

    def _terminate(self):
        with _utils.exit_on_exception(), self._signal_lock:
            self.terminate()
            sys.exit(0)

    def _run(self):
        with _utils.exit_on_exception():
            self.run()


class ServiceConfig(object):
    def __init__(self, service_id, service, workers, args, kwargs):
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

    def __init__(self, config, worker_id, graceful_shutdown_timeout):
        super(ServiceWorker, self).__init__()

        args = tuple() if config.args is None else config.args
        kwargs = dict() if config.kwargs is None else config.kwargs
        self.service = config.service(worker_id, *args, **kwargs)
        self.service._initialize(worker_id)
        if self.service.graceful_shutdown_timeout is None:
            self.service.graceful_shutdown_timeout = graceful_shutdown_timeout

        self.title = "%(name)s(%(worker_id)d) [%(pid)d]" % dict(
            name=self.service.name, worker_id=worker_id, pid=os.getpid())

        # Set process title
        setproctitle.setproctitle(
            "%(pname)s: %(name)s worker(%(worker_id)d)" % dict(
                pname=_utils.get_process_name(), name=self.service.name,
                worker_id=worker_id))

    def _on_signal_received(self, sig):
        # Code below must not block to return to select.select() and catch
        # next signals
        if sig == signal.SIGALRM:
            LOG.info('Graceful shutdown timeout (%d) exceeded, '
                     'exiting %s now.' %
                     (self.service.graceful_shutdown_timeout,
                      self.title))
            os._exit(1)

        elif sig == signal.SIGTERM:
            LOG.info('Caught SIGTERM signal, '
                     'graceful exiting of service %s' % self.title)

            if self.service.graceful_shutdown_timeout > 0:
                signal.alarm(self.service.graceful_shutdown_timeout)
            _utils.spawn(self.service._terminate)
        elif sig == signal.SIGHUP:
            _utils.spawn(self.service._reload)

    def wait_forever(self):
        LOG.debug("Run service %s" % self.title)
        _utils.spawn(self.service._run)
        super(ServiceWorker, self)._wait_forever()
