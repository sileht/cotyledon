===============================
Cotyledon
===============================

.. image:: https://travis-ci.org/sileht/cotyledon.png?branch=master
   :target: https://travis-ci.org/sileht/cotyledon

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
=============

This library is mainly used in Openstack Telemetry projects for now. In the past
oslo.service was used. But our projects don't want to use eventlet anymore.

oslo.service is written on top of eventlet to provide two main features:

* periodic tasks
* workers processes management

The first one was replaced by another Oslo lib called `futurist <http://docs.openstack.org/developer/futurist/>`_
and the second part by *Cotyledon*.

Our main issue was greenlet that doesn't run in timely fashion because we don't
monkeypatch the python stdlib anymore. Making `Tooz <http://docs.openstack.org/developer/tooz/>`_/`Oslo.messaging <http://docs.openstack.org/developer/oslo.messaging/>`_ hearbeats to fail.
And processes that doesn't exists as expected due to greenpipe never processed.

Unlike oslo.service, cotyledon have:

* The same code path when workers=1 and workers>=2
* reload API (on SIGHUP) hooks work in case of you don't want to restarting children
* a separated API for children process termination and for master process termination
* seatbelt to ensure only one service workers manager run at a time.
* Is signal concurrency safe.

And doesn't:

* facilitate the creation of wsgi application (sockets sharing between parent and children process). Because too many wsgi webserver already exists.

So these toohard to fix issues and the heavy eventlet dependencies make this
library to appear.
