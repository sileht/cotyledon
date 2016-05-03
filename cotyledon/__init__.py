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

import collections
import contextlib
import errno
import logging
import os
import random
import signal
import sys
import threading
import time

import setproctitle

LOG = logging.getLogger(__name__)

SIGNAL_TO_NAME = dict((getattr(signal, name), name) for name in dir(signal)
                      if name.startswith("SIG") and name not in ('SIG_DFL',
                                                                 'SIG_IGN'))


_ServiceConfig = collections.namedtuple("ServiceConfig", ["service",
                                                          "workers"])


def _spawn(target):
    t = threading.Thread(target=target)
    t.daemon = True
    t.start()
    return t


@contextlib.contextmanager
def _exit_on_exception():
    try:
        yield
    except SystemExit as exc:
        os._exit(exc.code)
    except BaseException:
        LOG.exception('Unhandled exception')
        os._exit(2)


class Service(object):
    """Base class for a service

    This class will be executed in a new child process of a
    :py:class:`ServiceRunner`. It registers signals to manager the reloading
    and the ending of the process.

    Methods :py:meth:`run`, :py:meth:`terminate` and :py:meth:`reload` are
    optional.
    """

    name = None
    """Service name used in the process title and the log messages in additionnal
    of the worker_id."""

    def __init__(self, worker_id):
        """Create a new Service

        :param worker_id: the identifier of this service instance
        :param worker_id: int
        """
        if self.name is None:
            self.name = self.__class__.__name__
        self.worker_id = worker_id
        self.pid = os.getpid()

        pname = os.path.basename(sys.argv[0])
        self._title = "%(name)s(%(worker_id)d) [%(pid)d]" % dict(
            name=self.name, worker_id=self.worker_id, pid=self.pid)

        # Set process title
        setproctitle.setproctitle(
            "%(pname)s - %(name)s(%(worker_id)d)" % dict(
                pname=pname, name=self.name,
                worker_id=self.worker_id))

        # Setup signals
        signal.signal(signal.SIGHUP, self._reload)
        signal.signal(signal.SIGTERM, self._clean_exit)

    def terminate(self):
        """Gracefully shutdown the service

        This method will be executed when the Service have to
        shutdown cleanly.

        If not implemented the process will just end with status 0.

        To customize the exit code SystemExit exception can be used.
        """

    def reload(self):
        """Reloading of the service

        This method will be executed when the Service receive a SIGHUP.

        If not implemented the process will just end with status 0 and
        :py:class:`ServiceRunner` will start a new fresh process for this
        service with the same worker_id.
        """
        self._clean_exit()

    def run(self):
        """Method representing the service activity

        If not implemented the process will just wait to receive an ending
        signal.
        """

    def _run(self):
        LOG.debug("Run service %s" % self._title)
        with _exit_on_exception():
            self.run()

    def _reload(self, sig, frame):
        with _exit_on_exception():
            self.reload()

    def _clean_exit(self, *args, **kwargs):
        LOG.info('Caught SIGTERM signal, '
                 'graceful exiting of service %s' % self._title)
        with _exit_on_exception():
            self.terminate()
        os._exit(0)


class ServiceManager(object):
    """Manage lifetimes of services

    :py:class:`ServiceManager` acts as a master process that controls the
    lifetime of children processes and restart them if they die unexpectedly.
    It also propagate some signals (SIGTERM, SIGALRM, SIGINT and SIGHUP) to
    them.

    Each child process runs an instance of a :py:class:`Service`.

    An application must create only one :py:class:ServiceManager` class
    and use :py:meth:ServiceManager.run()` as main loop of the application.



    Usage::

        class MyService(Service):
            def __init__(self):
                preparing_my_job()

            def run(self):
                do_my_job()

            def terminate(self):
                gracefully_stop_my_job()

            def reload(self):
                restart_my_job()

        sr = ServiceManager()
        sr.add("my_service", MyService, 5)
        sr.run()

    This will create 5 children processes running the service MyService.

    """

    _marker = object()
    _process_runner_already_created = False

    def __init__(self, wait_interval=0.01):
        """Creates the ServiceManager object

        :param wait_interval: time between each new process spawn
        :type wait_interval: float

        """

        if self._process_runner_already_created:
            raise RuntimeError("Only one instance of ProcessRunner per "
                               "application is allowed")
        ServiceManager._process_runner_already_created = True

        self._wait_interval = wait_interval
        self._shutdown = threading.Event()

        self._running_services = collections.defaultdict(dict)
        self._services = []
        self._forktimes = []
        self._current_process = None

        # Try to create a session id if possible
        try:
            os.setsid()
        except OSError:
            pass

        self.readpipe, self.writepipe = os.pipe()

        signal.signal(signal.SIGTERM, self._clean_exit)
        signal.signal(signal.SIGINT, self._fast_exit)
        signal.signal(signal.SIGALRM, self._alarm_exit)
        signal.signal(signal.SIGHUP, self._reload_services)

    def add(self, service, workers=1):
        """Add a new service to the ServiceManager

        :param service: callable that return an instance of :py:class:`Service`
        :type service: callable
        :param workers: number of processes/workers for this service
        :type workers: int
        """
        self._services.append(_ServiceConfig(service, workers))

    def run(self):
        """Start and supervise services

        This method will start and supervise all children processes
        until the master process asked to shutdown by a SIGTERM.

        All spawned processes are part of the same unix process group.
        """

        while not self._shutdown.is_set():
            info = self._wait_service()
            if info is not None:
                # Restart this particular service
                conf, worker_id = info
            else:
                for conf in self._services:
                    if len(self._running_services[conf]) < conf.workers:
                        worker_id = len(self._running_services[conf]) + 1
                        break
                else:
                    time.sleep(self._wait_interval)
                    continue

            pid = self._start_service(conf, worker_id)
            self._running_services[conf][pid] = worker_id

        LOG.debug("Killing services with signal SIGTERM")
        os.killpg(0, signal.SIGTERM)

        LOG.debug("Waiting services to terminate")
        os.waitpid(-1, 0)

        LOG.debug("Shutdown finish")
        os._exit(0)

    def _wait_service(self):
        """Return the last died service or None"""
        try:
            # Don't block if no child processes have exited
            pid, status = os.waitpid(0, os.WNOHANG)
            if not pid:
                return None
        except OSError as exc:
            if exc.errno not in (errno.EINTR, errno.ECHILD):
                raise
            return None

        if os.WIFSIGNALED(status):
            sig = SIGNAL_TO_NAME.get(os.WTERMSIG(status))
            LOG.info('Child %(pid)d killed by signal %(sig)s',
                     dict(pid=pid, sig=sig))
        else:
            code = os.WEXITSTATUS(status)
            LOG.info('Child %(pid)d exited with status %(code)d',
                     dict(pid=pid, code=code))

        for conf in self._running_services:
            if pid in self._running_services[conf]:
                return conf, self._running_services[conf].pop(pid)

        LOG.warning('pid %d not in service list', pid)

    def _reload_services(self, *args, **kwargs):
        if self._shutdown.is_set():
            # NOTE(sileht): We are in shutdown process no need
            # to reload anything
            return

        # Reset forktimes to respawn services quickly
        self._forktimes = []
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        os.killpg(0, signal.SIGHUP)
        signal.signal(signal.SIGHUP, self._reload_services)

    def _clean_exit(self, *args, **kwargs):
        # Don't need to be called more.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        LOG.info('Caught SIGTERM signal, graceful exiting of master process')
        self._shutdown.set()

    def _fast_exit(self, signo, frame):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
        LOG.info('Caught SIGINT signal, instantaneous exiting')
        os.killpg(0, signal.SIGINT)
        os._exit(1)

    def _alarm_exit(self, signo, frame):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
        LOG.info('Graceful shutdown timeout exceeded, '
                 'instantaneous exiting of master process')
        os.killpg(0, signal.SIGINT)
        os._exit(1)

    def _slowdown_respawn_if_needed(self):
        # Limit ourselves to one process a second (over the period of
        # number of workers * 1 second). This will allow workers to
        # start up quickly but ensure we don't fork off children that
        # die instantly too quickly.

        expected_children = sum(s.workers for s in self._services)
        if len(self._forktimes) > expected_children:
            if time.time() - self._forktimes[0] < expected_children:
                LOG.info('Forking too fast, sleeping')
                time.sleep(1)
                self._forktimes.pop(0)
                self._forktimes.append(time.time())

    def _start_service(self, config, worker_id):
        self._slowdown_respawn_if_needed()

        pid = os.fork()
        if pid != 0:
            return pid

        # reset parent signals
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)

        # Close write to ensure only parent has it open
        os.close(self.writepipe)

        _spawn(self._watch_parent_process)

        # Reseed random number generator
        random.seed()

        # Create and run a new service
        with _exit_on_exception():
            self._current_process = config.service(worker_id)
            _spawn(self._current_process._run)

        # Wait forever
        # NOTE(sileht): we cannot use threading.Event().wait() or
        # threading.Thread().join() because of
        # https://bugs.python.org/issue5315
        while True:
            time.sleep(1000000000)

    def _watch_parent_process(self):
        # This will block until the write end is closed when the parent
        # dies unexpectedly
        try:
            os.read(self.readpipe, 1)
        except EnvironmentError:
            pass

        LOG.info('Parent process has died unexpectedly, exiting')
        if self._current_process is not None:
            self._current_process._clean_exit()
        else:
            os._exit(0)
