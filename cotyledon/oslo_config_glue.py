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

import copy
import functools
import logging
import os

from oslo_config import cfg


LOG = logging.getLogger(__name__)

service_opts = [
    cfg.BoolOpt(
        "log_options",
        default=True,
        mutable=True,
        help="Enables or disables logging values of all "
        "registered options when starting a service (at DEBUG "
        "level).",
    ),
    cfg.IntOpt(
        "graceful_shutdown_timeout",
        mutable=True,
        default=60,
        help="Specify a timeout after which a gracefully shutdown "
        "server will exit. Zero value means endless wait.",
    ),
]


def _load_service_manager_options(service_manager, conf) -> None:
    service_manager.graceful_shutdown_timeout = conf.graceful_shutdown_timeout
    if conf.log_options:
        LOG.debug("Full set of CONF:")
        conf.log_opt_values(LOG, logging.DEBUG)


def _load_service_options(service, conf) -> None:
    service.graceful_shutdown_timeout = conf.graceful_shutdown_timeout

    if conf.log_options:
        LOG.debug("Full set of CONF:")
        conf.log_opt_values(LOG, logging.DEBUG)


def _configfile_reload(conf, reload_method) -> None:
    if reload_method == "reload":
        conf.reload_config_files()
    elif reload_method == "mutate":
        conf.mutate_config_files()


def _new_worker_hook(conf, reload_method, service_id, worker_id, service) -> None:
    def _service_reload(service) -> None:
        _configfile_reload(conf, reload_method)
        _load_service_options(service, conf)

    service._on_reload_internal_hook = _service_reload  # noqa: SLF001
    _load_service_options(service, conf)


def setup(service_manager, conf, reload_method="reload") -> None:
    """Load services configuration from oslo config object.

    It reads ServiceManager and Service configuration options from an
    oslo_config.ConfigOpts() object. Also It registers a ServiceManager hook to
    reload the configuration file on reload in the master process and in all
    children. And then when each child start or reload, the configuration
    options are logged if the oslo config option 'log_options' is True.

    On children, the configuration file is reloaded before the running the
    application reload method.

    Options currently supported on ServiceManager and Service:
    * graceful_shutdown_timeout

    :param service_manager: ServiceManager instance
    :type service_manager: cotyledon.ServiceManager
    :param conf: Oslo Config object
    :type conf: oslo_config.ConfigOpts()
    :param reload_method: reload or mutate the config files
    :type reload_method: str "reload/mutate"
    """
    conf.register_opts(service_opts)

    # Set cotyledon options from oslo config options
    _load_service_manager_options(service_manager, conf)

    def _service_manager_reload() -> None:
        _configfile_reload(conf, reload_method)
        _load_service_manager_options(service_manager, conf)

    if os.name != "posix":
        # NOTE(sileht): reloading can't be supported oslo.config is not pickle
        # But we don't care SIGHUP is not support on window
        return

    service_manager.register_hooks(
        on_new_worker=functools.partial(_new_worker_hook, conf, reload_method),
        on_reload=_service_manager_reload,
    )


def list_opts():
    """Entry point for oslo-config-generator."""
    return [(None, copy.deepcopy(service_opts))]
