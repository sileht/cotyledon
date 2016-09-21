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
import logging

from oslo_config import cfg

LOG = logging.getLogger(__name__)

service_opts = [
    cfg.BoolOpt('log_options',
                default=True,
                help='Enables or disables logging values of all '
                'registered options when starting a service (at DEBUG '
                'level).'),
    cfg.IntOpt('graceful_shutdown_timeout',
               default=60,
               help='Specify a timeout after which a gracefully shutdown '
               'server will exit. Zero value means endless wait.'),
]


def load_options(service, conf):
    """Load some service configuration from oslo config object."""
    conf.register_opts(service_opts)
    service.graceful_shutdown_timeout = conf.graceful_shutdown_timeout
    if conf.log_options:
        LOG.debug('Full set of CONF:')
        conf.log_opt_values(LOG, logging.DEBUG)


def list_opts():
    """Entry point for oslo-config-generator."""
    return [(None, copy.deepcopy(service_opts))]
