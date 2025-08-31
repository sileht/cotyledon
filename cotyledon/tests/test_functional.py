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

import os
import re
import signal
import subprocess
import threading
import time
import typing
import unittest

from cotyledon import oslo_config_glue


if os.name == "posix":

    def pid_exists(pid: int) -> bool:
        """Check whether pid exists in the current process table."""
        import errno  # noqa: PLC0415

        if pid < 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError as e:
            return e.errno == errno.EPERM
        else:
            return True
else:

    def pid_exists(pid: int) -> bool:
        import ctypes  # noqa: PLC0415

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        synchronize = 0x100000

        process = kernel32.OpenProcess(synchronize, 0, pid)
        if process != 0:
            kernel32.CloseHandle(process)
            return True
        return False


class Base(unittest.TestCase):
    name: typing.ClassVar[str]

    def setUp(self) -> None:
        super().setUp()

        self.lines: list[bytes] = []

        examplepy = os.path.join(os.path.dirname(__file__), "examples.py")
        self.subp = subprocess.Popen(
            ["python", examplepy, self.name],
            stdout=subprocess.PIPE,
            creationflags=0
            if os.name == "posix"
            else subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
            preexec_fn=os.setsid if os.name == "posix" else None,  # noqa:  PLW1509
        )

        self.t = threading.Thread(target=self.readlog)
        self.t.daemon = True
        self.t.start()

    def readlog(self) -> None:
        if self.subp.stdout is None:
            raise RuntimeError("Unexpected subprocess setup")  # noqa: EM101,TRY003
        while True:
            try:
                line = self.subp.stdout.readline().strip()
            except OSError:
                return
            if not line:
                continue
            # Keep only logger message
            if b":cotyledon." not in line:
                print(b"?? " + line)  # noqa: T201
                continue

            print(b"<> " + line)  # noqa: T201
            self.lines.append(line)

    def tearDown(self) -> None:
        if self.subp.poll() is None:
            self.subp.kill()
        super().tearDown()

    def get_lines(self, number: int | None = None) -> list[bytes]:
        if number is not None:
            while len(self.lines) < number:
                time.sleep(0.1)
            lines = self.lines[:number]
            del self.lines[:number]
            return lines
        self.subp.wait()
        # Wait children to terminate
        return self.lines

    @staticmethod
    def hide_pids(lines: list[bytes]) -> list[bytes]:
        return [
            re.sub(
                rb"Child \d+",
                b"Child XXXX",
                re.sub(rb" \[[^\]]*\]", b" [XXXX]", line),
            )
            for line in lines
        ]

    @staticmethod
    def get_pid(line: bytes) -> int:
        try:
            return int(line.split()[-1][1:-1])
        except Exception as exc:
            msg = f"Fail to find pid in {line.split()}"
            raise RuntimeError(msg) from exc


class TestCotyledon(Base):
    name = "example_app"

    def assert_everything_has_started(self) -> None:
        lines = sorted(self.get_lines(7))
        self.pid_heavy_1 = self.get_pid(lines[0])
        self.pid_heavy_2 = self.get_pid(lines[1])
        self.pid_light_1 = self.get_pid(lines[2])
        lines = self.hide_pids(lines)
        assert lines == [
            b"DEBUG:cotyledon._service_worker:Run service heavy(0) [XXXX]",
            b"DEBUG:cotyledon._service_worker:Run service heavy(1) [XXXX]",
            b"DEBUG:cotyledon._service_worker:Run service light(0) [XXXX]",
            b"ERROR:cotyledon.tests.examples:heavy init",
            b"ERROR:cotyledon.tests.examples:heavy init",
            b"ERROR:cotyledon.tests.examples:heavy run",
            b"ERROR:cotyledon.tests.examples:heavy run",
        ]

        self.assert_everything_is_alive()

    def assert_everything_is_alive(self) -> None:
        assert pid_exists(self.subp.pid)
        assert pid_exists(self.pid_light_1)
        assert pid_exists(self.pid_heavy_1)
        assert pid_exists(self.pid_heavy_2)

    def assert_everything_is_dead(self, status: int = 0) -> None:
        assert status == self.subp.poll()
        assert not pid_exists(self.subp.pid)
        assert not pid_exists(self.pid_light_1)
        assert not pid_exists(self.pid_heavy_1)
        assert not pid_exists(self.pid_heavy_2)

    @unittest.skipIf(os.name == "posix", "no window support")
    def test_workflow_window(self) -> None:
        # NOTE(sileht): The window workflow is a bit different because
        # SIGTERM doesn't really exists and processes are killed with SIGINT
        # TODO(sileht): Implements SIGBREAK to have graceful exists

        self.assert_everything_has_started()
        # Ensure we restart with terminate method exit code
        os.kill(self.pid_heavy_1, signal.SIGTERM)
        lines = self.get_lines(4)
        lines = self.hide_pids(lines)
        assert lines == [
            b"INFO:cotyledon._service_manager:Child XXXX exited with status 15",
            b"ERROR:cotyledon.tests.examples:heavy init",
            b"DEBUG:cotyledon._service_worker:Run service heavy(0) [XXXX]",
            b"ERROR:cotyledon.tests.examples:heavy run",
        ]

        # Kill master process
        os.kill(self.subp.pid, signal.SIGTERM)
        time.sleep(1)
        lines = self.get_lines()
        lines = sorted(self.hide_pids(lines))
        assert lines == [
            b"ERROR:cotyledon.tests.examples:heavy terminate",
            b"ERROR:cotyledon.tests.examples:heavy terminate",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service heavy(0) [XXXX]",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service heavy(1) [XXXX]",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service light(0) [XXXX]",
            b"INFO:cotyledon._service_worker:Parent process has died unexpectedly, heavy(0) [XXXX] exiting",
            b"INFO:cotyledon._service_worker:Parent process has died unexpectedly, heavy(1) [XXXX] exiting",
            b"INFO:cotyledon._service_worker:Parent process has died unexpectedly, light(0) [XXXX] exiting",
        ]

        assert self.subp.poll() == 15

    @unittest.skipIf(os.name != "posix", "no posix support")
    def test_workflow(self) -> None:
        self.assert_everything_has_started()

        # Ensure we just call reload method
        os.kill(self.pid_heavy_1, signal.SIGHUP)
        assert self.get_lines(1) == [b"ERROR:cotyledon.tests.examples:heavy reload"]

        # Ensure we restart because reload method is missing
        os.kill(self.pid_light_1, signal.SIGHUP)
        lines = self.get_lines(3)
        self.pid_light_1 = self.get_pid(lines[-1])
        lines = self.hide_pids(lines)
        assert lines == [
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service light(0) [XXXX]",
            b"INFO:cotyledon._service_manager:Child XXXX exited with status 0",
            b"DEBUG:cotyledon._service_worker:Run service light(0) [XXXX]",
        ]

        # Ensure we restart with terminate method exit code
        os.kill(self.pid_heavy_1, signal.SIGTERM)
        lines = self.get_lines(6)
        self.pid_heavy_1 = self.get_pid(lines[-2])
        lines = self.hide_pids(lines)
        assert lines == [
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service heavy(0) [XXXX]",
            b"ERROR:cotyledon.tests.examples:heavy terminate",
            b"INFO:cotyledon._service_manager:Child XXXX exited with status 42",
            b"ERROR:cotyledon.tests.examples:heavy init",
            b"DEBUG:cotyledon._service_worker:Run service heavy(0) [XXXX]",
            b"ERROR:cotyledon.tests.examples:heavy run",
        ]

        # Ensure we restart when no terminate method
        os.kill(self.pid_light_1, signal.SIGTERM)
        lines = self.get_lines(3)
        self.pid_light_1 = self.get_pid(lines[-1])
        lines = self.hide_pids(lines)
        assert lines == [
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service light(0) [XXXX]",
            b"INFO:cotyledon._service_manager:Child XXXX exited with status 0",
            b"DEBUG:cotyledon._service_worker:Run service light(0) [XXXX]",
        ]

        # Ensure everything is still alive
        self.assert_everything_is_alive()

        # Kill master process
        os.kill(self.subp.pid, signal.SIGTERM)

        lines = self.get_lines()
        assert lines[-1] == b"DEBUG:cotyledon._service_manager:Shutdown finish"
        time.sleep(1)
        lines = sorted(self.hide_pids(lines))
        assert lines == [
            b"DEBUG:cotyledon._service_manager:Killing services with signal SIGTERM",
            b"DEBUG:cotyledon._service_manager:Shutdown finish",
            b"DEBUG:cotyledon._service_manager:Waiting services to terminate",
            b"ERROR:cotyledon.tests.examples:heavy terminate",
            b"ERROR:cotyledon.tests.examples:heavy terminate",
            b"ERROR:cotyledon.tests.examples:master terminate hook",
            b"ERROR:cotyledon.tests.examples:master terminate2 hook",
            b"INFO:cotyledon._service_manager:Caught SIGTERM signal, graceful exiting of master process",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service heavy(0) [XXXX]",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service heavy(1) [XXXX]",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service light(0) [XXXX]",
        ]

        self.assert_everything_is_dead()

    @unittest.skipIf(os.name != "posix", "http://bugs.python.org/issue18040")
    def test_sigint(self) -> None:
        self.assert_everything_has_started()
        os.kill(self.subp.pid, signal.SIGINT)
        time.sleep(1)
        lines = sorted(self.get_lines())
        lines = self.hide_pids(lines)
        assert lines == [
            b"INFO:cotyledon._service_manager:Caught SIGINT signal, instantaneous exiting",
            b"INFO:cotyledon._service_worker:Caught SIGINT signal, instantaneous exiting of service heavy(0) [XXXX]",
            b"INFO:cotyledon._service_worker:Caught SIGINT signal, instantaneous exiting of service heavy(1) [XXXX]",
            b"INFO:cotyledon._service_worker:Caught SIGINT signal, instantaneous exiting of service light(0) [XXXX]",
        ]
        self.assert_everything_is_dead(1)

    @unittest.skipIf(os.name != "posix", "no posix support")
    def test_sighup(self) -> None:
        self.assert_everything_has_started()
        os.kill(self.subp.pid, signal.SIGHUP)
        time.sleep(0.5)
        lines = sorted(self.get_lines(6))
        lines = self.hide_pids(lines)
        assert lines == [
            b"DEBUG:cotyledon._service_worker:Run service light(0) [XXXX]",
            b"ERROR:cotyledon.tests.examples:heavy reload",
            b"ERROR:cotyledon.tests.examples:heavy reload",
            b"ERROR:cotyledon.tests.examples:master reload hook",
            b"INFO:cotyledon._service_manager:Child XXXX exited with status 0",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service light(0) [XXXX]",
        ]

        os.kill(self.subp.pid, signal.SIGINT)
        time.sleep(0.5)
        self.assert_everything_is_dead(1)

    @unittest.skipIf(os.name != "posix", "no posix support")
    def test_sigkill(self) -> None:
        self.assert_everything_has_started()
        self.subp.kill()
        time.sleep(1)
        lines = sorted(self.get_lines())
        lines = self.hide_pids(lines)
        assert lines == [
            b"ERROR:cotyledon.tests.examples:heavy terminate",
            b"ERROR:cotyledon.tests.examples:heavy terminate",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service heavy(0) [XXXX]",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service heavy(1) [XXXX]",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service light(0) [XXXX]",
            b"INFO:cotyledon._service_worker:Parent process has died unexpectedly, heavy(0) [XXXX] exiting",
            b"INFO:cotyledon._service_worker:Parent process has died unexpectedly, heavy(1) [XXXX] exiting",
            b"INFO:cotyledon._service_worker:Parent process has died unexpectedly, light(0) [XXXX] exiting",
        ]
        self.assert_everything_is_dead(-9)


class TestBadlyCodedCotyledon(Base):
    name = "badly_coded_app"

    @unittest.skipIf(os.name != "posix", "no posix support")
    def test_badly_coded(self) -> None:
        time.sleep(2)
        self.subp.terminate()
        time.sleep(2)
        assert self.subp.poll() == 0, self.get_lines()
        assert not pid_exists(self.subp.pid)


class TestBuggyCotyledon(Base):
    name = "buggy_app"

    @unittest.skipIf(os.name != "posix", "no posix support")
    def test_graceful_timeout_term(self) -> None:
        lines = self.get_lines(1)
        childpid = self.get_pid(lines[0])
        self.subp.terminate()
        time.sleep(2)
        assert self.subp.poll() == 0
        assert not pid_exists(self.subp.pid)
        assert not pid_exists(childpid)
        lines = self.hide_pids(self.get_lines())
        assert b"ERROR:cotyledon.tests.examples:time.sleep done" not in lines
        assert lines[-2:] == [
            b"INFO:cotyledon._service_worker:Graceful shutdown timeout (1) exceeded, exiting buggy(0) [XXXX] now.",
            b"DEBUG:cotyledon._service_manager:Shutdown finish",
        ]

    @unittest.skipIf(os.name != "posix", "no posix support")
    def test_graceful_timeout_kill(self) -> None:
        lines = self.get_lines(1)
        childpid = self.get_pid(lines[0])
        self.subp.kill()
        time.sleep(2)
        assert self.subp.poll() == -9
        assert not pid_exists(self.subp.pid)
        assert not pid_exists(childpid)
        lines = self.hide_pids(self.get_lines())
        assert b"ERROR:cotyledon.tests.examples:time.sleep done" not in lines
        assert lines[-3:] == [
            b"INFO:cotyledon._service_worker:Parent process has died unexpectedly, buggy(0) [XXXX] exiting",
            b"INFO:cotyledon._service_worker:Caught SIGTERM signal, graceful exiting of service buggy(0) [XXXX]",
            b"INFO:cotyledon._service_worker:Graceful shutdown timeout (1) exceeded, exiting buggy(0) [XXXX] now.",
        ]


class TestOsloCotyledon(Base):
    name = "oslo_app"

    def test_options(self) -> None:
        options = oslo_config_glue.list_opts()
        assert len(options) == 1
        assert None is options[0][0]
        assert len(options[0][1]) == 2

        lines = self.get_lines(1)
        assert b"DEBUG:cotyledon.oslo_config_glue:Full set of CONF:" in lines
        self.subp.terminate()


class TestTermDuringStartupCotyledon(Base):
    name = "sigterm_during_init"

    def test_sigterm(self) -> None:
        lines = self.hide_pids(self.get_lines())
        assert b"DEBUG:cotyledon._service_manager:Shutdown finish" in lines
