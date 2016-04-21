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
import sys
import threading

import cotyledon

LOG = logging.getLogger(__name__)


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


def example_app():
    logging.basicConfig(level=logging.DEBUG)
    p = cotyledon.ServiceManager()
    p.add(FullService, 2)
    p.add(LigthService)
    p.run()
