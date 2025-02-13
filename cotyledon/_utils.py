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
import errno
import logging
import multiprocessing
import os
import select
import signal
import sys
import threading
import typing


if typing.TYPE_CHECKING:
    import types


if os.name == "posix":
    import fcntl

LOG = logging.getLogger(__name__)


_SIGNAL_TO_NAME: dict[int, str] = {
    getattr(signal, name): name
    for name in dir(signal)
    if name.startswith("SIG") and isinstance(getattr(signal, name), signal.Signals)
}

SIGNAL_WAKEUP_FD_READ_SIZE = 4096


def signal_to_name(sig: int) -> str:
    return _SIGNAL_TO_NAME.get(sig, str(sig))


P = typing.ParamSpec("P")
R = typing.TypeVar("R")


def spawn(
    target: typing.Callable[P, R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> threading.Thread:
    t = threading.Thread(target=target, args=args, kwargs=kwargs)
    t.daemon = True
    t.start()
    return t


def check_workers(workers: int, minimum: int) -> None:
    if not isinstance(workers, int) or workers < minimum:
        msg = f"'workers' must be an int >= {minimum}, not: {workers} ({type(workers).__name__})"
        raise ValueError(msg)


def check_callable(
    thing: typing.Any,  # noqa: ANN401
    name: str,
) -> None:
    if not callable(thing):
        msg = f"'{name}' must be a callable"
        raise TypeError(msg)


def spawn_process(
    target: typing.Callable[P, R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> multiprocessing.Process:
    p = multiprocessing.Process(
        target=target,
        args=args,
        kwargs=kwargs,
    )
    p.start()
    return p


setproctitle: typing.Callable[[str], None] | None
try:
    from setproctitle import setproctitle
except ImportError:
    setproctitle = None


def set_process_title(title: str) -> None:
    if setproctitle is None:
        return

    setproctitle(title)


def get_process_name() -> str:
    return os.path.basename(sys.argv[0])


def run_hooks(
    name: str,
    hooks: list[typing.Callable[P, R]],
    *args: P.args,
    **kwargs: P.kwargs,
) -> None:
    try:
        for hook in hooks:
            hook(*args, **kwargs)
    except Exception:
        LOG.exception("Exception raised during %s hooks", name)


@contextlib.contextmanager
def exit_on_exception() -> typing.Iterator[None]:
    try:
        yield
    except SystemExit as exc:
        # mypy is sick, it's a int, not int|str|None
        os._exit(exc.code)  # type: ignore[arg-type]
    except BaseException:
        LOG.exception("Unhandled exception")
        os._exit(2)


if os.name == "posix":
    SIGALRM = signal.SIGALRM
    SIGHUP = signal.SIGHUP
    SIGCHLD = signal.SIGCHLD
else:
    SIGALRM = SIGHUP = SIGCHLD = None  # type: ignore[assignment]


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
            # NOTE(sileht): is mypy posix only ?
            signal.signal(signal.SIGBREAK, self._signal_catcher)  # type: ignore[attr-defined]

    @staticmethod
    def _set_nonblock(fd: int) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL, 0)
        flags |= os.O_NONBLOCK
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)

    @staticmethod
    def _set_autoclose(fd: int) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)

    def _signal_catcher(self, sig: int, frame: types.FrameType | None) -> None:
        # Needed to receive the signal via set_wakeup_fd
        pass

    def _wait_forever(self) -> None:
        # Wait forever
        while True:
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
                    self._on_signal_received(sig)

                if len(signals) < SIGNAL_WAKEUP_FD_READ_SIZE:
                    break

    def _on_signal_received(self, sig: int) -> None:
        pass
