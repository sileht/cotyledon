========
API
========

.. autoclass:: cotyledon.Service
   :members:
   :special-members: __init__

.. autoclass:: cotyledon.ServiceManager
   :members:
   :special-members: __init__

.. autofunction:: cotyledon.oslo_config_glue.load_options


Note about non posix support
----------------------------

On non-posix platform the lib have some limitation.

When the master process receives a signal, the propagation to children
processes is done manually on known pids. This is because non-posix platform
doesn't have notion of processes group and we can't just send the signal to the
process group.

Also signal handlers are only run every 5 seconds instead of just after the
signal reception because non-posix platform does not support
signal.set_wakeup_fd correctly

And to finish, the processes names are not set on non-posix platform.
