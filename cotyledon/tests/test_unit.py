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

import mock

import cotyledon
from cotyledon.tests import base


class FakeService(cotyledon.Service):
    pass


class SomeTest(base.TestCase):
    def test_forking_slowdown(self):
        sm = cotyledon.ServiceManager()
        sm.add(FakeService, workers=3)
        with mock.patch('time.sleep') as sleep:
            sm._slowdown_respawn_if_needed()
            sm._slowdown_respawn_if_needed()
            sm._slowdown_respawn_if_needed()
            # We simulatge 3 more spawn
            sm._slowdown_respawn_if_needed()
            sm._slowdown_respawn_if_needed()
            sm._slowdown_respawn_if_needed()
            self.assertEqual(2, len(sleep.mock_calls))