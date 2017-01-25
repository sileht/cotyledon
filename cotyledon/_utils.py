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
import multiprocessing
import os
import select
import signal
import sys
import threading
import time

if os.name == 'posix':
    import fcntl

LOG = logging.getLogger(__name__)


_SIGNAL_TO_NAME = dict((getattr(signal, name), name) for name in dir(signal)
                       if name.startswith("SIG") and name not in ('SIG_DFL',
                                                                  'SIG_IGN'))


def signal_to_name(sig):
    return _SIGNAL_TO_NAME.get(sig)


def spawn(target, *args, **kwargs):
    t = threading.Thread(target=target, args=args, kwargs=kwargs)
    t.daemon = True
    t.start()
    return t


def check_workers(workers, minimum):
    if not isinstance(workers, int) or workers < minimum:
        raise ValueError("'workers' must be an int >= %d, not: %s (%s)" %
                         (minimum, workers, type(workers).__name__))


def check_callable(thing, name):
    if not hasattr(thing, "__call__"):
        raise ValueError("'%s' must be a callable" % name)


def _bootstrap_process(target, *args, **kwargs):
    if "fds_to_close" in kwargs:
        for fd in kwargs["fds_to_close"]:
            os.close(fd)
        del kwargs["fds_to_close"]
    target(*args, **kwargs)


def spawn_process(*args, **kwargs):
    p = multiprocessing.Process(target=_bootstrap_process,
                                args=args, kwargs=kwargs)
    p.start()
    return p


try:
    from setproctitle import setproctitle
except ImportError:
    def setproctitle(*args, **kwargs):
        pass


def get_process_name():
    return os.path.basename(sys.argv[0])


def run_hooks(name, hooks, *args, **kwargs):
    try:
        for hook in hooks:
            hook(*args, **kwargs)
    except Exception:
        LOG.exception("Exception raised during %s hooks" % name)


@contextlib.contextmanager
def exit_on_exception():
    try:
        yield
    except SystemExit as exc:
        os._exit(exc.code)
    except BaseException:
        LOG.exception('Unhandled exception')
        os._exit(2)


if os.name == "posix":
    SIGALRM = signal.SIGALRM
    SIGHUP = signal.SIGHUP
    SIGCHLD = signal.SIGCHLD
    SIBREAK = None
else:
    SIGALRM = SIGHUP = None
    SIGCHLD = "fake sigchld"
    SIGBREAK = signal.SIGBREAK


class SignalManager(object):
    def __init__(self, master=False):
        # Setup signal fd, this allows signal to behave correctly
        if os.name == 'posix':
            self.signal_pipe_r, self.signal_pipe_w = os.pipe()
            self._set_nonblock(self.signal_pipe_r)
            self._set_nonblock(self.signal_pipe_w)
            signal.set_wakeup_fd(self.signal_pipe_w)

        self._signals_received = collections.deque()

        signal.signal(signal.SIGINT, signal.SIG_DFL)
        if os.name == 'posix':
            signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, self._signal_catcher)
            signal.signal(signal.SIGALRM, self._signal_catcher)
            signal.signal(signal.SIGHUP, self._signal_catcher)
        else:
            # currently a noop on window...
            signal.signal(signal.SIGTERM, self._signal_catcher)
            # FIXME(sileht): should allow to catch signal CTRL_BREAK_EVENT,
            # but we to create the child process with CREATE_NEW_PROCESS_GROUP
            # to make this work, so current this is a noop for later fix
            signal.signal(signal.SIGBREAK, self._signal_catcher)

    @staticmethod
    def _set_nonblock(fd):
        flags = fcntl.fcntl(fd, fcntl.F_GETFL, 0)
        flags = flags | os.O_NONBLOCK
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)

    def _signal_catcher(self, sig, frame):
        # NOTE(sileht): This is useful only for python < 3.5
        # in python >= 3.5 we could read the signal number
        # from the wakeup_fd pipe
        if sig in (SIGALRM, signal.SIGTERM):
            self._signals_received.appendleft(sig)
        else:
            self._signals_received.append(sig)

    def _wait_forever(self):
        # Wait forever
        while True:
            # Check if signals have been received
            if os.name == "posix":
                self._empty_signal_pipe()
            self._run_signal_handlers()

            if os.name == "posix":
                # NOTE(sileht): we cannot use threading.Event().wait(),
                # threading.Thread().join(), or time.sleep() because signals
                # can be missed when received by non-main threads
                # (https://bugs.python.org/issue5315)
                # So we use select.select() alone, we will receive EINTR or
                # will read data from signal_r when signal is emitted and
                # cpython calls PyErr_CheckSignals() to run signals handlers
                # That looks perfect to ensure handlers are run and run in the
                # main thread
                try:
                    select.select([self.signal_pipe_r], [], [])
                except select.error as e:
                    if e.args[0] != errno.EINTR:
                        raise
            else:
                # NOTE(sileht): here we do only best effort
                # and wake the loop periodically, set_wakeup_fd
                # doesn't work on non posix platform so
                # 1 seconds have been picked with the advice of a dice.
                time.sleep(1)
                # NOTE(sileht): We emulate SIGCHLD, _service_manager
                # will just check often for dead child
                self._signals_received.append(SIGCHLD)

    def _empty_signal_pipe(self):
        try:
            while os.read(self.signal_pipe_r, 4096) == 4096:
                pass
        except (IOError, OSError):
            pass

    def _run_signal_handlers(self):
        while True:
            try:
                sig = self._signals_received.popleft()
            except IndexError:
                return
            self._on_signal_received(sig)

    def _on_signal_received(self, sig):
        pass
