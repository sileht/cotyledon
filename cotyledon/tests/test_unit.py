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

from unittest import mock

import pytest

import cotyledon
from cotyledon.tests import base


class FakeService(cotyledon.Service):
    pass


class SomeTest(base.TestCase):
    def setUp(self) -> None:
        super().setUp()
        cotyledon.ServiceManager._process_runner_already_created = False

    def test_forking_slowdown(self) -> None:  # noqa: PLR6301
        sm = cotyledon.ServiceManager()
        sm.add(FakeService, workers=3)
        with mock.patch("time.sleep") as sleep:
            sm._slowdown_respawn_if_needed()
            sm._slowdown_respawn_if_needed()
            sm._slowdown_respawn_if_needed()
            # We simulatge 3 more spawn
            sm._slowdown_respawn_if_needed()
            sm._slowdown_respawn_if_needed()
            sm._slowdown_respawn_if_needed()
            assert len(sleep.mock_calls) == 6

    def test_invalid_service(self) -> None:
        sm = cotyledon.ServiceManager()

        self.assert_raises_msg(
            TypeError,
            "'service' must be a callable",
            sm.add,
            "foo",
        )
        self.assert_raises_msg(
            ValueError,
            "'workers' must be an int >= 1, not: None (NoneType)",
            sm.add,
            FakeService,
            workers=None,
        )
        self.assert_raises_msg(
            ValueError,
            "'workers' must be an int >= 1, not: -2 (int)",
            sm.add,
            FakeService,
            workers=-2,
        )

        oid = sm.add(FakeService, workers=3)
        self.assert_raises_msg(
            ValueError,
            "'workers' must be an int >= -2, not: -5 (int)",
            sm.reconfigure,
            oid,
            workers=-5,
        )
        self.assert_raises_msg(
            ValueError,
            "notexists service id doesn't exists",
            sm.reconfigure,
            "notexists",
            workers=-1,
        )

    @staticmethod
    def assert_raises_msg(exc, msg, func, *args, **kwargs) -> None:
        with pytest.raises(exc) as exc_info:
            func(*args, **kwargs)
        assert msg == str(exc_info.value)
