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
import fcntl
import logging
import os
import select
import signal
import sys
import threading

LOG = logging.getLogger(__name__)


_SIGNAL_TO_NAME = dict((getattr(signal, name), name) for name in dir(signal)
                       if name.startswith("SIG") and name not in ('SIG_DFL',
                                                                  'SIG_IGN'))


def signal_to_name(sig):
    return _SIGNAL_TO_NAME.get(sig)


def spawn(target):
    t = threading.Thread(target=target)
    t.daemon = True
    t.start()
    return t


def get_process_name():
    return os.path.basename(sys.argv[0])


@contextlib.contextmanager
def exit_on_exception():
    try:
        yield
    except SystemExit as exc:
        os._exit(exc.code)
    except BaseException:
        LOG.exception('Unhandled exception')
        os._exit(2)


class SignalManager(object):
    def __init__(self, wakeup_interval=None):
        self._wakeup_interval = wakeup_interval
        # Setup signal fd, this allows signal to behave correctly
        self.signal_pipe_r, self.signal_pipe_w = os.pipe()
        self._set_nonblock(self.signal_pipe_r)
        self._set_nonblock(self.signal_pipe_w)
        signal.set_wakeup_fd(self.signal_pipe_w)

        self._signals_received = collections.deque()

        signal.signal(signal.SIGHUP, self._signal_catcher)
        signal.signal(signal.SIGTERM, self._signal_catcher)
        signal.signal(signal.SIGALRM, self._signal_catcher)

    @staticmethod
    def _set_nonblock(fd):
        flags = fcntl.fcntl(fd, fcntl.F_GETFL, 0)
        flags = flags | os.O_NONBLOCK
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)

    def _signal_catcher(self, sig, frame):
        if sig in [signal.SIGALRM, signal.SIGTERM]:
            self._signals_received.appendleft(sig)
        else:
            self._signals_received.append(sig)

    def _wait_forever(self):
        # Wait forever
        while True:
            # Check if signals have been received
            self._empty_signal_pipe()
            self._run_signal_handlers()

            # Run Wakeup hook
            self._on_wakeup()

            # NOTE(sileht): we cannot use threading.Event().wait(),
            # threading.Thread().join(), or time.sleep() because signals
            # can be missed when received by non-main threads
            # (https://bugs.python.org/issue5315)
            # So we use select.select() alone, we will receive EINTR or will
            # read data from signal_r when signal is emitted and cpython calls
            # PyErr_CheckSignals() to run signals handlers That looks perfect
            # to ensure handlers are run and run in the main thread
            try:
                select.select([self.signal_pipe_r], [], [],
                              self._wakeup_interval)
            except select.error as e:
                if e.args[0] != errno.EINTR:
                    raise

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

    def _on_wakeup(self):
        pass

    def _on_signal_received(self, sig):
        pass
