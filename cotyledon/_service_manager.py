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
import os
import multiprocessing
import signal
import socket
import sys
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
                self.register_hooks(on_reload=selfreload)

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
        super(ServiceManager, self).__init__(wait_interval)

        # We use OrderedDict to start services in adding order
        self._services = collections.OrderedDict()
        self._running_services = collections.defaultdict(dict)
        self._forktimes = []
        self._graceful_shutdown_timeout = graceful_shutdown_timeout

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
        """

        if on_terminate is not None:
            self._hooks['terminate'].append(on_terminate)
        if on_reload is not None:
            self._hooks['reload'].append(on_reload)
        if on_new_worker is not None:
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
        except IndexError:
            raise ValueError("%s service id doesn't exists" % service_id)
        else:
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
        self._wait_forever()

    def _on_wakeup(self):
        dead_pid = self._get_last_pid_died()
        while dead_pid is not None:
            self._restart_dead_worker(dead_pid)
            dead_pid = self._get_last_pid_died()
        self._adjust_workers()

    def _on_signal_received(self, sig):
        if sig == getattr(signal, 'SIGALRM', None):
            self._fast_exit(reason='Graceful shutdown timeout exceeded, '
                            'instantaneous exiting of master process')
        elif sig == signal.SIGTERM:
            self._shutdown()
        elif sig == getattr(signal, 'SIGHUP', None):
            self._reload()

    def _reload(self):
        self._run_hooks('reload')

        # Reset forktimes to respawn services quickly
        self._forktimes = []
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        self._killpg(signal.SIGHUP)
        signal.signal(signal.SIGHUP, self._signal_catcher)

    def _killpg(self, sig):
        "Send 'sig' to the process group"
        if os.name == "posix":
            os.killpg(0, sig)
        else:
            # We do only best effort by killing our known children
            # and that's all
            for processes in self._running_services.values():
                for process in processes:
                    os.kill(process.pid, sig)

    def _shutdown(self):
        LOG.info('Caught SIGTERM signal, graceful exiting of master process')

        if self._graceful_shutdown_timeout > 0:
            signal.alarm(self._graceful_shutdown_timeout)

        self._run_hooks('terminate')

        LOG.debug("Killing services with signal SIGTERM")
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        self._killpg(signal.SIGTERM)

        LOG.debug("Waiting services to terminate")
        for processes in self._running_services.values():
            for process in processes:
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

    def _restart_dead_worker(self, dead_pid):
        for service_id in self._running_services:
            # We copy the list to clean the orignal one
            processes = list(self._running_services[service_id].items())
            for process, worker_id in processes:
                if process.pid == dead_pid:
                    del self._running_services[service_id][process]
                    self._start_worker(service_id, worker_id)
                    return
        LOG.error('pid %d not in service known pids list', dead_pid)

    def _get_last_pid_died(self):
        """Return the last died service or None"""
        for service_id, processes in self._running_services.items():
            for process, worker_id in processes.items():
                if not process.is_alive():
                    if process.exitcode < 0:
                        sig = _utils.signal_to_name(process.exitcode)
                        LOG.info('Child %(pid)d killed by signal %(sig)s',
                                 dict(pid=process.pid, sig=sig))
                    else:
                        LOG.info('Child %(pid)d exited with status %(code)d',
                                 dict(pid=process.pid, code=process.exitcode))
                    return process.pid

    def _fast_exit(self, signo=None, frame=None,
                   reason='Caught SIGINT signal, instantaneous exiting'):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if os.name == 'posix':
            signal.signal(signal.SIGALRM, signal.SIG_IGN)
        LOG.info(reason)
        self._killpg(signal.SIGINT)
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
                time.sleep(1)
                self._forktimes.pop(0)
                self._forktimes.append(time.time())

    def _start_worker(self, service_id, worker_id):
        self._slowdown_respawn_if_needed()

        # FIXME(sileht): It's better to close them in child process
        # os.close(self.signal_pipe_r)
        # os.close(self.signal_pipe_w)

        # Create and run a new service
        p = _utils.spawn_process(
            _service.ServiceWorker.create_and_wait,
            self._services[service_id],
            service_id,
            worker_id,
            self._death_detection_pipe,
            self._hooks['new_worker'],
            self._graceful_shutdown_timeout)

        self._running_services[service_id][p] = worker_id

    def _stop_worker(self, service_id, worker_id):
        for process, _id in self._running_services[service_id].items():
            if _id == worker_id:
                os.kill(process.pid, signal.SIGTERM)

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
