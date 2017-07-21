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
import socket
import sys
import threading
import time

from oslo_config import cfg

import cotyledon
from cotyledon import _utils
from cotyledon import oslo_config_glue

if len(sys.argv) >= 3:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("127.0.0.1", int(sys.argv[2])))
    if os.name == "posix":
        stream = os.fdopen(s.fileno(), 'w')
    else:
        stream = s.makefile()
    logging.basicConfig(level=logging.DEBUG, stream=stream)
else:
    logging.basicConfig(level=logging.DEBUG)

LOG = logging.getLogger("cotyledon.tests.examples")

# We don't want functional tests to wait for this:
cotyledon.ServiceManager._slowdown_respawn_if_needed = lambda *args: True


class FullService(cotyledon.Service):
    name = "heavy"

    def __init__(self, worker_id):
        super(FullService, self).__init__(worker_id)
        self._shutdown = threading.Event()
        LOG.error("%s init" % self.name)

    def run(self):
        LOG.error("%s run" % self.name)
        self._shutdown.wait()

    def terminate(self):
        LOG.error("%s terminate" % self.name)
        self._shutdown.set()
        sys.exit(42)

    def reload(self):
        LOG.error("%s reload" % self.name)


class LigthService(cotyledon.Service):
    name = "light"


class BuggyService(cotyledon.Service):
    name = "buggy"
    graceful_shutdown_timeout = 1

    def terminate(self):
        time.sleep(60)
        LOG.error("time.sleep done")


class BadlyCodedService(cotyledon.Service):
    def run(self):
        raise Exception("so badly coded service")


class OsloService(cotyledon.Service):
    name = "oslo"


class WindowService(cotyledon.Service):
    name = "window"


def on_terminate():
    LOG.error("master terminate hook")


def on_terminate2():
    LOG.error("master terminate2 hook")


def on_reload():
    LOG.error("master reload hook")


def example_app():
    p = cotyledon.ServiceManager()
    p.add(FullService, 2)
    service_id = p.add(LigthService, 5)
    p.reconfigure(service_id, 1)
    p.register_hooks(on_terminate, on_reload)
    p.register_hooks(on_terminate2)
    p.run()


def buggy_app():
    p = cotyledon.ServiceManager()
    p.add(BuggyService)
    p.run()


def oslo_app():
    conf = cfg.ConfigOpts()
    conf([], project='openstack-app', validate_default_values=True,
         version="0.1")

    p = cotyledon.ServiceManager()
    oslo_config_glue.setup(p, conf)
    p.add(OsloService)
    p.run()


def window_sanity_check():
    p = cotyledon.ServiceManager()
    p.add(LigthService)
    t = _utils.spawn(p.run)
    time.sleep(10)
    os.kill(os.getpid(), signal.SIGTERM)
    t.join()


def badly_coded_app():
    p = cotyledon.ServiceManager()
    p.add(BadlyCodedService)
    p.run()


if __name__ == '__main__':
    globals()[sys.argv[1]]()
