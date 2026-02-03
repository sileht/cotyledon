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

import multiprocessing
import typing
from unittest import TestCase
from unittest import mock

import pytest

import cotyledon
from cotyledon import _utils


P = typing.ParamSpec("P")
R = typing.TypeVar("R")


class FakeService(cotyledon.Service):
    pass


class SomeTest(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cotyledon.ServiceManager._process_runner_already_created = False

    def test_forking_slowdown(self) -> None:
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
            "foo",  # type: ignore[arg-type]
        )
        self.assert_raises_msg(
            ValueError,
            "'workers' must be an int >= 1, not: None (NoneType)",
            sm.add,
            FakeService,
            workers=None,  # type: ignore[arg-type]
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
            "notexists",  # type: ignore[arg-type]
            workers=-1,
        )

    @staticmethod
    def assert_raises_msg(
        exc: type[Exception] | tuple[type[Exception], ...],
        msg: str,
        func: typing.Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        with pytest.raises(exc) as exc_info:
            func(*args, **kwargs)
        assert msg == str(exc_info.value)

    def test_set_graceful_shutdown_timeout(self) -> None:
        sm = cotyledon.ServiceManager()
        # Check default value
        assert sm._graceful_shutdown_timeout == 60

        sm.set_graceful_shutdown_timeout(120)
        assert sm._graceful_shutdown_timeout == 120

        sm.set_graceful_shutdown_timeout(0)
        assert sm._graceful_shutdown_timeout == 0


class TestMultiprocessing(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cotyledon.ServiceManager._process_runner_already_created = False

    def test_multiprocessing_context_default(self) -> None:
        """Test that ServiceManager uses multiprocessing (fork) by default"""
        sm = cotyledon.ServiceManager()
        # Default should be multiprocessing context (fork-compatible)
        assert sm.mp_context == multiprocessing.get_context()

    def test_multiprocessing_context_constructor(self) -> None:
        """Test that mp_context parameter in constructor works"""
        spawn_ctx = multiprocessing.get_context("spawn")
        sm = cotyledon.ServiceManager(mp_context=spawn_ctx)
        assert sm.mp_context is spawn_ctx

    def test_spawn_process_with_context(self) -> None:
        """Test that spawn_process uses the provided context"""

        def dummy_target() -> None:
            pass

        # Test with default context (None)
        mock_proc_instance = mock.Mock()
        mock_proc_instance.start = mock.Mock()
        with mock.patch(
            "multiprocessing.Process",
            return_value=mock_proc_instance,
        ) as mock_process:
            _utils.spawn_process(dummy_target)
            # Should use multiprocessing.Process
            mock_process.assert_called_once()
            mock_proc_instance.start.assert_called_once()

        # Test with explicit context
        spawn_ctx = multiprocessing.get_context("spawn")
        mock_ctx_proc_instance = mock.Mock()
        mock_ctx_proc_instance.start = mock.Mock()
        with mock.patch.object(
            spawn_ctx,
            "Process",
            return_value=mock_ctx_proc_instance,
        ) as mock_ctx_process:
            _utils.spawn_process(dummy_target, ctx=spawn_ctx)
            # Should use ctx.Process
            mock_ctx_process.assert_called_once()
            mock_ctx_proc_instance.start.assert_called_once()

    def test_spawn_process_backward_compatible(self) -> None:
        """Test that spawn_process without ctx is backward compatible"""

        def dummy_target() -> None:
            pass

        # When ctx is None, should default to multiprocessing
        mock_proc_instance = mock.Mock()
        mock_proc_instance.start = mock.Mock()
        with mock.patch(
            "multiprocessing.Process",
            return_value=mock_proc_instance,
        ) as mock_process:
            _utils.spawn_process(dummy_target, ctx=None)
            mock_process.assert_called_once()
            mock_proc_instance.start.assert_called_once()
