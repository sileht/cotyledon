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


if os.name == "posix":
    import fcntl

LOG = logging.getLogger(__name__)


_SIGNAL_TO_NAME = {
    getattr(signal, name): name
    for name in dir(signal)
    if name.startswith("SIG") and isinstance(getattr(signal, name), signal.Signals)
}

SIGNAL_WAKEUP_FD_READ_SIZE = 4096


def signal_to_name(sig):
    return _SIGNAL_TO_NAME.get(sig, sig)


def spawn(target, *args, **kwargs):
    t = threading.Thread(target=target, args=args, kwargs=kwargs)
    t.daemon = True
    t.start()
    return t


def check_workers(workers, minimum) -> None:
    if not isinstance(workers, int) or workers < minimum:
        msg = f"'workers' must be an int >= {minimum}, not: {workers} ({type(workers).__name__})"
        raise ValueError(msg)


def check_callable(thing, name) -> None:
    if not callable(thing):
        msg = f"'{name}' must be a callable"
        raise TypeError(msg)


def spawn_process(target, *args, **kwargs):
    p = multiprocessing.Process(
        target=target,
        args=args,
        kwargs=kwargs,
    )
    p.start()
    return p


try:
    from setproctitle import setproctitle
except ImportError:

    def setproctitle(*args, **kwargs) -> None:
        pass


def get_process_name():
    return os.path.basename(sys.argv[0])


def run_hooks(name, hooks, *args, **kwargs) -> None:
    try:
        for hook in hooks:
            hook(*args, **kwargs)
    except Exception:
        LOG.exception("Exception raised during %s hooks", name)


@contextlib.contextmanager
def exit_on_exception():
    try:
        yield
    except SystemExit as exc:
        os._exit(exc.code)
    except BaseException:
        LOG.exception("Unhandled exception")
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


class SignalManager:
    def __init__(self) -> None:
        # Setup signal fd, this allows signal to behave correctly
        if os.name == "posix":
            self.signal_pipe_r, self.signal_pipe_w = os.pipe()
            self._set_nonblock(self.signal_pipe_r)
            self._set_nonblock(self.signal_pipe_w)
            self._set_autoclose(self.signal_pipe_r)
            self._set_autoclose(self.signal_pipe_w)
            signal.set_wakeup_fd(self.signal_pipe_w)

        self._pid = os.getpid()
        self._signals_received = collections.deque()

        if os.name == "posix":
            signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            signal.signal(signal.SIGINT, self._signal_catcher)
            signal.signal(signal.SIGTERM, self._signal_catcher)
            signal.signal(signal.SIGALRM, self._signal_catcher)
            signal.signal(signal.SIGHUP, self._signal_catcher)
        else:
            signal.signal(signal.SIGINT, self._signal_catcher)
            # currently a noop on window...
            signal.signal(signal.SIGTERM, self._signal_catcher)
            # TODO(sileht): should allow to catch signal CTRL_BREAK_EVENT,
            # but we to create the child process with CREATE_NEW_PROCESS_GROUP
            # to make this work, so current this is a noop for later fix
            signal.signal(signal.SIGBREAK, self._signal_catcher)

    @staticmethod
    def _set_nonblock(fd) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL, 0)
        flags |= os.O_NONBLOCK
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)

    @staticmethod
    def _set_autoclose(fd) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)

    def _signal_catcher(self, sig, frame) -> None:
        # Needed to receive the signal via set_wakeup_fd
        pass

    def _wait_forever(self) -> None:
        # Wait forever
        while True:
            # Check if signals have been received
            self._run_signal_handlers()

            # NOTE(sileht): signals can be missed when received by non-main threads
            # (https://bugs.python.org/issue5315)
            # We use signal.set_wakeup_fd + select.select() which is more reliable
            try:
                select.select([self.signal_pipe_r], [], [])
            except OSError as e:
                if e.args[0] == errno.EINTR:
                    raise

            while True:
                try:
                    signals = os.read(self.signal_pipe_r, SIGNAL_WAKEUP_FD_READ_SIZE)
                except OSError:
                    break

                for sig in signals:
                    if sig in {SIGALRM, signal.SIGTERM, signal.SIGINT}:
                        self._signals_received.appendleft(sig)
                    else:
                        self._signals_received.append(sig)

                if len(signals) < SIGNAL_WAKEUP_FD_READ_SIZE:
                    break

    def _run_signal_handlers(self) -> None:
        while True:
            try:
                sig = self._signals_received.popleft()
            except IndexError:
                return
            self._on_signal_received(sig)

    def _on_signal_received(self, sig) -> None:
        pass
