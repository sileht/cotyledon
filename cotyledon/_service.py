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

import logging
import os
import signal
import typing


if typing.TYPE_CHECKING:
    from cotyledon import types


LOG = logging.getLogger(__name__)


class Service:
    """Base class for a service

    This class will be executed in a new child process/worker
    :py:class:`ServiceWorker` of a :py:class:`ServiceManager`. It registers
    signals to manager the reloading and the ending of the process.

    Methods :py:meth:`run`, :py:meth:`terminate` and :py:meth:`reload` are
    optional.
    """

    name: typing.ClassVar[str | None] = None
    """Service name used in the process title and the log messages in
    additional of the worker_id."""

    graceful_shutdown_timeout: typing.ClassVar[int | None] = None
    """Timeout after which a gracefully shutdown service will exit. zero means
    endless wait. None means same as ServiceManager that launch the service"""

    def __init_subclass__(cls, **kwargs: typing.Any) -> None:  # noqa: ANN401
        super().__init_subclass__(**kwargs)
        if cls.name is None:
            cls.name = cls.__name__

    def __init__(self, worker_id: types.WorkerId) -> None:
        """Create a new Service

        :param worker_id: the identifier of this service instance
        :type worker_id: int

        The identifier of the worker can be used for workload repartition
        because it's consistent and always the same.

        For example, if the number of workers for this service is 3,
        one will got 0, the second got 1 and the last got 2.
        if worker_id 1 died, the new spawned process will got 1 again.
        """
        super().__init__()
        self.worker_id = worker_id

    def _noop_hook(self, service: Service) -> None:
        pass

    def terminate(self) -> None:
        """Gracefully shutdown the service

        This method will be executed when the Service has to shutdown cleanly.

        If not implemented the process will just end with status 0.

        To customize the exit code, the :py:class:`SystemExit` exception can be
        used.

        Any exceptions raised by this method will be logged and the worker will
        exit with status 1.
        """

    def reload(self) -> None:  # noqa: PLR6301
        """Reloading of the service

        This method will be executed when the Service receives a SIGHUP.

        If not implemented the process will just end with status 0 and
        :py:class:`ServiceRunner` will start a new fresh process for this
        service with the same worker_id.

        Any exceptions raised by this method will be logged and the worker will
        exit with status 1.
        """
        os.kill(os.getpid(), signal.SIGTERM)

    def run(self) -> None:
        """Method representing the service activity

        If not implemented the process will just wait to receive an ending
        signal.

        This method is ran into the thread and can block or return as needed

        Any exceptions raised by this method will be logged and the worker will
        exit with status 1.
        """
