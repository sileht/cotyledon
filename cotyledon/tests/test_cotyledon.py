# -*- coding: utf-8 -*-

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

import os
import re
import signal
import subprocess
import time

from cotyledon import oslo_config_glue
from cotyledon.tests import base


class Base(base.TestCase):
    def setUp(self):
        super(Base, self).setUp()
        examplepy = os.path.join(os.path.dirname(__file__),
                                 "examples.py")
        self.subp = subprocess.Popen(['python', examplepy, self.name],
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT,
                                     close_fds=True,
                                     preexec_fn=os.setsid)

    def tearDown(self):
        if self.subp.poll() is None:
            os.kill(self.subp.pid, signal.SIGINT)
        super(Base, self).tearDown()

    def get_lines(self, number=None):
        if number is not None:
            return [self.subp.stdout.readline().strip() for i in
                    range(number)]
        else:
            return [l.strip() for l in self.subp.stdout.readlines()]

    @staticmethod
    def hide_pids(lines):
        return [re.sub(b"Child \d+", b"Child XXXX",
                       re.sub(b" \[[^\]]*\]", b" [XXXX]", line))
                for line in lines]

    @staticmethod
    def get_pid(line):
        try:
            return int(line.split()[-1][1:-1])
        except Exception:
            raise Exception('Fail to find pid in %s' % line.split())


class TestCotyledon(Base):
    name = 'example_app'

    def assert_everything_has_started(self):
        lines = sorted(self.get_lines(7))
        self.pid_heavy_1 = self.get_pid(lines[0])
        self.pid_heavy_2 = self.get_pid(lines[1])
        self.pid_light_1 = self.get_pid(lines[2])
        lines = self.hide_pids(lines)
        self.assertEqual([
            b'DEBUG:cotyledon._service:Run service heavy(0) [XXXX]',
            b'DEBUG:cotyledon._service:Run service heavy(1) [XXXX]',
            b'DEBUG:cotyledon._service:Run service light(0) [XXXX]',
            b'ERROR:cotyledon.tests.examples:heavy init',
            b'ERROR:cotyledon.tests.examples:heavy init',
            b'ERROR:cotyledon.tests.examples:heavy run',
            b'ERROR:cotyledon.tests.examples:heavy run'
        ], lines)

        self.assert_everything_is_alive()

    def assert_everything_is_alive(self):
        os.kill(self.subp.pid, 0)
        os.kill(self.pid_light_1, 0)
        os.kill(self.pid_heavy_1, 0)
        os.kill(self.pid_heavy_2, 0)

    def assert_everything_is_dead(self, status=0):
        self.assertEqual(status, self.subp.poll())
        self.assertRaises(OSError, os.kill, self.subp.pid, 0)
        self.assertRaises(OSError, os.kill, self.pid_heavy_2, 0)
        self.assertRaises(OSError, os.kill, self.pid_heavy_1, 0)
        self.assertRaises(OSError, os.kill, self.pid_light_1, 0)

    def test_workflow(self):
        self.assert_everything_has_started()

        # Ensure we just call reload method
        os.kill(self.pid_heavy_1, signal.SIGHUP)
        self.assertEqual(b"ERROR:cotyledon.tests.examples:heavy reload",
                         self.subp.stdout.readline().strip())

        # Ensure we restart because reload method is missing
        os.kill(self.pid_light_1, signal.SIGHUP)
        lines = self.get_lines(3)
        self.pid_light_1 = self.get_pid(lines[-1])
        lines = self.hide_pids(lines)
        self.assertEqual([
            b'INFO:cotyledon._service:Caught SIGTERM signal, graceful '
            b'exiting of service light(0) [XXXX]',
            b'INFO:cotyledon._service_manager:Child XXXX exited with status 0',
            b'DEBUG:cotyledon._service:Run service light(0) [XXXX]'
        ], lines)

        # Ensure we restart with terminate method exit code
        os.kill(self.pid_heavy_1, signal.SIGTERM)
        lines = self.get_lines(6)
        self.pid_heavy_1 = self.get_pid(lines[-2])
        lines = self.hide_pids(lines)
        self.assertEqual([
            b'INFO:cotyledon._service:Caught SIGTERM signal, graceful '
            b'exiting of service heavy(0) [XXXX]',
            b'ERROR:cotyledon.tests.examples:heavy terminate',
            b'INFO:cotyledon._service_manager:Child XXXX exited '
            b'with status 42',
            b'ERROR:cotyledon.tests.examples:heavy init',
            b'DEBUG:cotyledon._service:Run service heavy(0) [XXXX]',
            b'ERROR:cotyledon.tests.examples:heavy run',
        ], lines)

        # Ensure we restart when no terminate method
        os.kill(self.pid_light_1, signal.SIGTERM)
        lines = self.get_lines(3)
        self.pid_light_1 = self.get_pid(lines[-1])
        lines = self.hide_pids(lines)
        self.assertEqual([
            b'INFO:cotyledon._service:Caught SIGTERM signal, graceful '
            b'exiting of service light(0) [XXXX]',
            b'INFO:cotyledon._service_manager:Child XXXX exited with status 0',
            b'DEBUG:cotyledon._service:Run service light(0) [XXXX]',
        ], lines)

        # Ensure everthing is still alive
        os.kill(self.subp.pid, 0)
        os.kill(self.pid_light_1, 0)
        os.kill(self.pid_heavy_1, 0)
        os.kill(self.pid_heavy_2, 0)

        # Kill master process
        os.kill(self.subp.pid, signal.SIGTERM)
        self.subp.terminate()
        lines = self.get_lines()
        self.assertEqual(b'DEBUG:cotyledon._service_manager:Shutdown finish',
                         lines[-1])
        time.sleep(0.5)
        lines = sorted(self.hide_pids(lines))
        self.assertEqual([
            b'DEBUG:cotyledon._service_manager:Killing services with '
            b'signal SIGTERM',
            b'DEBUG:cotyledon._service_manager:Shutdown finish',
            b'DEBUG:cotyledon._service_manager:Waiting services to terminate',
            b'ERROR:cotyledon.tests.examples:heavy terminate',
            b'ERROR:cotyledon.tests.examples:heavy terminate',
            b'ERROR:cotyledon.tests.examples:master terminate hook',
            b'ERROR:cotyledon.tests.examples:master terminate2 hook',
            b'INFO:cotyledon._service:Caught SIGTERM signal, '
            b'graceful exiting of service heavy(0) [XXXX]',
            b'INFO:cotyledon._service:Caught SIGTERM signal, '
            b'graceful exiting of service heavy(1) [XXXX]',
            b'INFO:cotyledon._service:Caught SIGTERM signal, '
            b'graceful exiting of service light(0) [XXXX]',
            b'INFO:cotyledon._service_manager:Caught SIGTERM signal, '
            b'graceful exiting of master process',
        ], lines)

        self.assert_everything_is_dead()

    def test_sigint(self):
        self.assert_everything_has_started()
        os.kill(self.subp.pid, signal.SIGINT)
        time.sleep(0.5)
        lines = sorted(self.get_lines())
        lines = self.hide_pids(lines)
        self.assertEqual([
            b'INFO:cotyledon._service_manager:Caught SIGINT signal, '
            b'instantaneous exiting',
        ], lines)
        self.assert_everything_is_dead(1)

    def test_sighup(self):
        self.assert_everything_has_started()
        os.kill(self.subp.pid, signal.SIGHUP)
        time.sleep(0.5)
        lines = sorted(self.get_lines(6))
        lines = self.hide_pids(lines)
        self.assertEqual([
            b'DEBUG:cotyledon._service:Run service light(0) [XXXX]',
            b'ERROR:cotyledon.tests.examples:heavy reload',
            b'ERROR:cotyledon.tests.examples:heavy reload',
            b'ERROR:cotyledon.tests.examples:master reload hook',
            b'INFO:cotyledon._service:Caught SIGTERM signal, '
            b'graceful exiting of service light(0) [XXXX]',
            b'INFO:cotyledon._service_manager:Child XXXX exited with status 0'
        ], lines)

        os.kill(self.subp.pid, signal.SIGINT)
        time.sleep(0.5)
        self.assert_everything_is_dead(1)

    def test_sigkill(self):
        self.assert_everything_has_started()
        self.subp.kill()
        time.sleep(0.5)
        lines = sorted(self.get_lines())
        lines = self.hide_pids(lines)
        self.assertEqual([
            b'ERROR:cotyledon.tests.examples:heavy terminate',
            b'ERROR:cotyledon.tests.examples:heavy terminate',
            b'INFO:cotyledon._service:Caught SIGTERM signal, '
            b'graceful exiting of service heavy(0) [XXXX]',
            b'INFO:cotyledon._service:Caught SIGTERM signal, '
            b'graceful exiting of service heavy(1) [XXXX]',
            b'INFO:cotyledon._service:Caught SIGTERM signal, '
            b'graceful exiting of service light(0) [XXXX]',
            b'INFO:cotyledon._service_manager:Parent process has died '
            b'unexpectedly, heavy(0) [XXXX] exiting',
            b'INFO:cotyledon._service_manager:Parent process has died '
            b'unexpectedly, heavy(1) [XXXX] exiting',
            b'INFO:cotyledon._service_manager:Parent process has died '
            b'unexpectedly, light(0) [XXXX] exiting',
        ], lines)
        self.assert_everything_is_dead(-9)


class TestBuggyCotyledon(Base):
    name = "buggy_app"

    def test_graceful_timeout_term(self):
        lines = self.get_lines(1)
        childpid = self.get_pid(lines[0])
        self.subp.terminate()
        time.sleep(2)
        self.assertEqual(0, self.subp.poll())
        self.assertRaises(OSError, os.kill, self.subp.pid, 0)
        self.assertRaises(OSError, os.kill, childpid, 0)
        lines = self.hide_pids(self.get_lines())
        self.assertNotIn('ERROR:cotyledon.tests.examples:time.sleep done',
                         lines)
        self.assertEqual([
            b'INFO:cotyledon._service:Graceful shutdown timeout (1) exceeded, '
            b'exiting buggy(0) [XXXX] now.',
            b'DEBUG:cotyledon._service_manager:Shutdown finish'
        ], lines[-2:])

    def test_graceful_timeout_kill(self):
        lines = self.get_lines(1)
        childpid = self.get_pid(lines[0])
        self.subp.kill()
        time.sleep(2)
        self.assertEqual(-9, self.subp.poll())
        self.assertRaises(OSError, os.kill, self.subp.pid, 0)
        self.assertRaises(OSError, os.kill, childpid, 0)
        lines = self.hide_pids(self.get_lines())
        self.assertNotIn('ERROR:cotyledon.tests.examples:time.sleep done',
                         lines)
        self.assertEqual([
            b'INFO:cotyledon._service_manager:Parent process has died '
            b'unexpectedly, buggy(0) [XXXX] exiting',
            b'INFO:cotyledon._service:Caught SIGTERM signal, graceful exiting '
            b'of service buggy(0) [XXXX]',
            b'INFO:cotyledon._service:Graceful shutdown timeout (1) exceeded, '
            b'exiting buggy(0) [XXXX] now.',
        ], lines[-3:])


class TestOsloCotyledon(Base):
    name = "oslo_app"

    def test_options(self):
        options = oslo_config_glue.list_opts()
        self.assertEqual(1, len(options))
        self.assertEqual(None, options[0][0])
        self.assertEqual(2, len(options[0][1]))

        lines = self.get_lines(1)
        self.assertEqual(
            b'DEBUG:cotyledon.oslo_config_glue:Full set of CONF:',
            lines[0])
        self.subp.terminate()
