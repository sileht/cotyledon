============================
Note about non posix support
============================

On non-posix platform the lib have some limitation.

When the master process receives a signal, the propagation to children
processes is done manually on known pids instead of the process group.

SIGHUP is of course not supported.

Processes termination are not done gracefully. Even we use Popen.terminate(),
children don't received SIGTERM/SIGBREAK as expected. The module
multiprocessing doesn't allow to set CREATE_NEW_PROCESS_GROUP on new processes
and catch SIGBREAK.

Also signal handlers are only run every second instead of just after the
signal reception because non-posix platform does not support
signal.set_wakeup_fd correctly

And to finish, the processes names are not set on non-posix platform.
