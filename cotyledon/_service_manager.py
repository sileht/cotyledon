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
import logging
import multiprocessing
import os
import signal
import socket
import sys
import threading
import time
import uuid


from cotyledon import _service
from cotyledon import _utils

LOG = logging.getLogger(__name__)


class ServiceManager(_utils.SignalManager):
    """Manage lifetimes of services

    :py:class:`ServiceManager` acts as a master process that controls the
    lifetime of children processes and restart them if they die unexpectedly.
    It also propagate some signals (SIGTERM, SIGALRM, SIGINT and SIGHUP) to
    them.

    Each child process (:py:class:`ServiceWorker`) runs an instance of
    a :py:class:`Service`.

    An application must create only one :py:class:`ServiceManager` class and
    use :py:meth:`ServiceManager.run()` as main loop of the application.



    Usage::

        class MyService(Service):
            def __init__(self, worker_id, myconf):
                super(MyService, self).__init__(worker_id)
                preparing_my_job(myconf)
                self.running = True

            def run(self):
                while self.running:
                    do_my_job()

            def terminate(self):
                self.running = False
                gracefully_stop_my_jobs()

            def reload(self):
                restart_my_job()


        class MyManager(ServiceManager):
            def __init__(self):
                super(MyManager, self).__init__()
                self.register_hooks(on_reload=self.reload)

                conf = {'foobar': 2}
                self.service_id = self.add(MyService, 5, conf)

            def reload(self):
                self.reconfigure(self.service_id, 10)

        MyManager().run()

    This will create 5 children processes running the service MyService.

    """

    _process_runner_already_created = False

    def __init__(self, wait_interval=0.01, graceful_shutdown_timeout=60):
        """Creates the ServiceManager object

        :param wait_interval: time between each new process spawn
        :type wait_interval: float

        """

        if self._process_runner_already_created:
            raise RuntimeError("Only one instance of ServiceManager per "
                               "application is allowed")
        ServiceManager._process_runner_already_created = True
        super(ServiceManager, self).__init__()

        # We use OrderedDict to start services in adding order
        self._services = collections.OrderedDict()
        self._running_services = collections.defaultdict(dict)
        self._forktimes = []
        self._graceful_shutdown_timeout = graceful_shutdown_timeout
        self._wait_interval = wait_interval

        self._dead = threading.Event()
        # NOTE(sileht): Set it on startup, so first iteration
        # will spawn initial workers
        self._got_sig_chld = threading.Event()
        self._got_sig_chld.set()

        self._child_supervisor = None

        self._hooks = {
            'terminate': [],
            'reload': [],
            'new_worker': [],
        }

        _utils.setproctitle("%s: master process [%s]" %
                            (_utils.get_process_name(), " ".join(sys.argv)))

        # Try to create a session id if possible
        try:
            os.setsid()
        except (OSError, AttributeError):
            pass

        self._death_detection_pipe = multiprocessing.Pipe(duplex=False)

        signal.signal(signal.SIGINT, self._fast_exit)
        if os.name == 'posix':
            signal.signal(signal.SIGCHLD, self._signal_catcher)

    def register_hooks(self, on_terminate=None, on_reload=None,
                       on_new_worker=None):
        """Register hook methods

        This can be callable multiple times to add more hooks, hooks are
        executed in added order. If a hook raised an exception, next hooks
        will be not executed.

        :param on_terminate: method called on SIGTERM
        :type on_terminate: callable()
        :param on_reload: method called on SIGHUP
        :type on_reload: callable()
        :param on_new_worker: method called in the child process when this one
                              is ready
        :type on_new_worker: callable(service_id, worker_id, service_obj)

        If window support is planned, hooks callable must support
        to be pickle.pickle(). See CPython multiprocessing module documentation
        for more detail.
        """

        if on_terminate is not None:
            _utils.check_callable(on_terminate, 'on_terminate')
            self._hooks['terminate'].append(on_terminate)
        if on_reload is not None:
            _utils.check_callable(on_reload, 'on_reload')
            self._hooks['reload'].append(on_reload)
        if on_new_worker is not None:
            _utils.check_callable(on_new_worker, 'on_new_worker')
            self._hooks['new_worker'].append(on_new_worker)

    def _run_hooks(self, name, *args, **kwargs):
        _utils.run_hooks(name, self._hooks[name], *args, **kwargs)

    def add(self, service, workers=1, args=None, kwargs=None):
        """Add a new service to the ServiceManager

        :param service: callable that return an instance of :py:class:`Service`
        :type service: callable
        :param workers: number of processes/workers for this service
        :type workers: int
        :param args: additional positional arguments for this service
        :type args: tuple
        :param kwargs: additional keywoard arguments for this service
        :type kwargs: dict

        :return: a service id
        :rtype: uuid.uuid4
        """
        _utils.check_callable(service, 'service')
        _utils.check_workers(workers, 1)
        service_id = uuid.uuid4()
        self._services[service_id] = _service.ServiceConfig(
            service_id, service, workers, args, kwargs)
        return service_id

    def reconfigure(self, service_id, workers):
        """Reconfigure a service registered in ServiceManager

        :param service_id: the service id
        :type service_id: uuid.uuid4
        :param workers: number of processes/workers for this service
        :type workers: int
        :raises: ValueError
        """
        try:
            sc = self._services[service_id]
        except KeyError:
            raise ValueError("%s service id doesn't exists" % service_id)
        else:
            _utils.check_workers(workers, minimum=(1 - sc.workers))
            sc.workers = workers
            # Reset forktimes to respawn services quickly
            self._forktimes = []

    def run(self):
        """Start and supervise services workers

        This method will start and supervise all children processes
        until the master process asked to shutdown by a SIGTERM.

        All spawned processes are part of the same unix process group.
        """

        self._systemd_notify_once()
        self._child_supervisor = _utils.spawn(self._child_supervisor_thread)
        self._wait_forever()

    def _child_supervisor_thread(self):
        while not self._dead.is_set():
            self._got_sig_chld.wait()
            self._got_sig_chld.clear()

            if self._dead.is_set():
                    return

            info = self._get_last_worker_died()
            while info is not None:
                service_id, worker_id = info
                self._start_worker(service_id, worker_id)
                info = self._get_last_worker_died()
                if self._dead.is_set():
                    return

            self._adjust_workers()

    def _on_signal_received(self, sig):
        if sig == _utils.SIGALRM:
            self._alarm()
        elif sig == signal.SIGTERM:
            self._shutdown()
        elif sig == _utils.SIGHUP:
            self._reload()
        elif sig == _utils.SIGCHLD:
            self._got_sig_chld.set()
        else:
            LOG.debug("unhandled signal %s" % sig)

    def _alarm(self):
        self._fast_exit(reason='Graceful shutdown timeout exceeded, '
                        'instantaneous exiting of master process')

    def _reload(self):
        """reload all children

        posix only
        """
        self._run_hooks('reload')

        # Reset forktimes to respawn services quickly
        self._forktimes = []
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        os.killpg(0, signal.SIGHUP)
        signal.signal(signal.SIGHUP, self._signal_catcher)

    def _shutdown(self):
        LOG.info('Caught SIGTERM signal, graceful exiting of master process')
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

        if self._graceful_shutdown_timeout > 0:
            if os.name == "posix":
                signal.alarm(self._graceful_shutdown_timeout)
            else:
                threading.Timer(self._graceful_shutdown_timeout,
                                self._alarm).start()

        # NOTE(sileht): Stop the child supervisor
        self._dead.set()
        self._got_sig_chld.set()
        self._child_supervisor.join()

        self._run_hooks('terminate')

        LOG.debug("Killing services with signal SIGTERM")
        if os.name == 'posix':
            os.killpg(0, signal.SIGTERM)

        LOG.debug("Waiting services to terminate")
        for processes in self._running_services.values():
            for process in processes:
                if os.name != "posix":
                    # NOTE(sileht): we don't have killpg so we
                    # kill all known processes instead
                    # FIXME(sileht): We should use CTRL_BREAK_EVENT on windows
                    # when CREATE_NEW_PROCESS_GROUP will be set on child
                    # process
                    process.terminate()
                process.join()

        LOG.debug("Shutdown finish")
        sys.exit(0)

    def _adjust_workers(self):
        for service_id, conf in self._services.items():
            running_workers = len(self._running_services[service_id])
            if running_workers < conf.workers:
                for worker_id in range(running_workers, conf.workers):
                    self._start_worker(service_id, worker_id)
            elif running_workers > conf.workers:
                for worker_id in range(running_workers, conf.workers):
                    self._stop_worker(service_id, worker_id)

    def _get_last_worker_died(self):
        """Return the last died worker information or None"""
        for service_id in self._running_services:
            # We copy the list to clean the orignal one
            processes = list(self._running_services[service_id].items())
            for process, worker_id in processes:
                if not process.is_alive():
                    if process.exitcode < 0:
                        sig = _utils.signal_to_name(process.exitcode)
                        LOG.info('Child %(pid)d killed by signal %(sig)s',
                                 dict(pid=process.pid, sig=sig))
                    else:
                        LOG.info('Child %(pid)d exited with status %(code)d',
                                 dict(pid=process.pid, code=process.exitcode))
                    del self._running_services[service_id][process]
                    return service_id, worker_id

    def _fast_exit(self, signo=None, frame=None,
                   reason='Caught SIGINT signal, instantaneous exiting'):
        if os.name == 'posix':
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGALRM, signal.SIG_IGN)
            LOG.info(reason)
            os.killpg(0, signal.SIGINT)
        else:
            # NOTE(sileht): On windows killing the master process
            # with SIGINT kill automatically children
            LOG.info(reason)
        os._exit(1)

    def _slowdown_respawn_if_needed(self):
        # Limit ourselves to one process a second (over the period of
        # number of workers * 1 second). This will allow workers to
        # start up quickly but ensure we don't fork off children that
        # die instantly too quickly.
        expected_children = sum(s.workers for s in self._services.values())
        if len(self._forktimes) > expected_children:
            if time.time() - self._forktimes[0] < expected_children:
                LOG.info('Forking too fast, sleeping')
                time.sleep(5)
            self._forktimes.pop(0)
        else:
            time.sleep(self._wait_interval)
        self._forktimes.append(time.time())

    def _start_worker(self, service_id, worker_id):
        self._slowdown_respawn_if_needed()

        if os.name == "posix":
            fds = [self.signal_pipe_w, self.signal_pipe_r]
        else:
            fds = []

        # Create and run a new service
        p = _utils.spawn_process(
            _service.ServiceWorker.create_and_wait,
            self._services[service_id],
            service_id,
            worker_id,
            self._death_detection_pipe,
            self._hooks['new_worker'],
            self._graceful_shutdown_timeout,
            fds_to_close=fds)

        self._running_services[service_id][p] = worker_id

    def _stop_worker(self, service_id, worker_id):
        for process, _id in self._running_services[service_id].items():
            if _id == worker_id:
                # FIXME(sileht): We should use CTRL_BREAK_EVENT on windows
                # when CREATE_NEW_PROCESS_GROUP will be set on child process
                process.terminte()

    @staticmethod
    def _systemd_notify_once():
        """Send notification once to Systemd that service is ready.

        Systemd sets NOTIFY_SOCKET environment variable with the name of the
        socket listening for notifications from services.
        This method removes the NOTIFY_SOCKET environment variable to ensure
        notification is sent only once.
        """

        notify_socket = os.getenv('NOTIFY_SOCKET')
        if notify_socket:
            if notify_socket.startswith('@'):
                # abstract namespace socket
                notify_socket = '\0%s' % notify_socket[1:]
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            with contextlib.closing(sock):
                try:
                    sock.connect(notify_socket)
                    sock.sendall(b'READY=1')
                    del os.environ['NOTIFY_SOCKET']
                except EnvironmentError:
                    LOG.debug("Systemd notification failed", exc_info=True)
