===============================
Cotyledon
===============================

.. image:: https://img.shields.io/pypi/v/cotyledon.svg
   :target: https://pypi.python.org/pypi/cotyledon/
   :alt: Latest Version

.. image:: https://img.shields.io/pypi/dm/cotyledon.svg
   :target: https://pypi.python.org/pypi/cotyledon/
   :alt: Downloads

Cotyledon provides a framework for defining long-running services.

It provides handling of Unix signals, spawning of workers, supervision of
children processes, daemon reloading, sd-notify, rate limiting for worker
spawning, and more.

* Free software: Apache license
* Documentation: http://cotyledon.readthedocs.org/
* Source: https://github.com/sileht/cotyledon
* Bugs: https://github.com/sileht/cotyledon/issues

Why Cotyledon
-------------

This library is mainly used in OpenStack Telemetry projects, in replacement of
*oslo.service*. However, as *oslo.service* depends on *eventlet*, a different
library was needed for project that do not need it. When an application do not
monkeypatch the Python standard library anymore, greenlets do not in timely
fashion. That made other libraries such as `Tooz
<http://docs.openstack.org/developer/tooz/>`_ or `oslo.messaging
<http://docs.openstack.org/developer/oslo.messaging/>`_ to fail with e.g. their
heartbeat systems. Also, processes would not exist as expected due to
greenpipes never being processed.

*oslo.service* is actually written on top of eventlet to provide two main
features:

* periodic tasks
* workers processes management

The first feature was replaced by another library called `futurist
<http://docs.openstack.org/developer/futurist/>`_ and the second feature is
superseded by *Cotyledon*.

Unlike *oslo.service*, **Cotyledon** have:

* The same code path when workers=1 and workers>=2
* Reload API (on SIGHUP) hooks work in case of you don't want to restarting children
* A separated API for children process termination and for master process termination
* Seatbelt to ensure only one service workers manager run at a time.
* Is signal concurrency safe.
* Support non posix platform, because it's built on top of multiprocessing module
  instead of os.fork
* Provide functional testing

And doesn't:

* facilitate the creation of wsgi application (sockets sharing between parent
  and children process). Because too many wsgi webserver already exists.

*oslo.service* being impossible to fix and bringing an heavy dependency on
eventlet, **Cotyledon** appeared.
