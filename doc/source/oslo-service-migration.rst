===============================
Oslo.service migration examples
===============================

This example shows the same application with oslo.service and cotyledon.
It uses a wide range of API of oslo.service, but most applications don't
really uses all of this. In most case cotyledon.ServiceManager don't
need to inherited.

It doesn't show how to replace the periodic task API, if you use it
you should take a look to `futurist documentation`_


oslo.service typical application:

.. code-block:: python

    import multiprocessing
    from oslo.service import service
    from oslo.config import cfg

    class MyService(service.Service):
        def __init__(self, conf):
            # called before os.fork()
            self.conf = conf
            self.master_pid = os.getpid()

            self.queue = multiprocessing.Queue()

        def start(self):
            # called when application start (parent process start)
            # and
            # called just after os.fork()

            if self.master_pid == os.getpid():
                do_master_process_start()
            else:
                task = self.queue.get()
                do_child_process_start(task)


        def stop(self):
            # called when children process stop
            # and
            # called when application stop (parent process stop)
            if self.master_pid == os.getpid():
                do_master_process_stop()
            else:
                do_child_process_stop()

        def restart(self):
            # called on SIGHUP
            if self.master_pid == os.getpid():
                do_master_process_reload()
            else:
                # Can't be reach oslo.service currently prefers to
                # kill the child process for safety purpose
                do_child_process_reload()

    class MyOtherService(service.Service):
        pass


    class MyThirdService(service.Service):
        pass


    def main():
        conf = cfg.ConfigOpts()
        service = MyService(conf)
        launcher = service.launch(conf, service, workers=2, restart_method='reload')
        launcher.launch_service(MyOtherService(), worker=conf.other_workers)

        # Obviously not recommanded, because two objects will handle the
        # lifetime of the masterp process but some application does this, so...
        launcher2 = service.launch(conf, MyThirdService(), workers=2, restart_method='restart')

        launcher.wait()
        launcher2.wait()

        # Here, we have no way to change the number of worker dynamically.


Cotyledon version of the typical application:

.. code-block:: python

    import cotyledon
    from cotyledon import oslo_config_glue

    class MyService(cotyledon.Service):
        name = "MyService fancy name that will showup in 'ps xaf'"

        # Everything in this object will be called after os.fork()
        def __init__(self, worker_id, conf, queue):
            self.conf = conf
            self.queue = queue

        def run(self):
            # Optional method to run the child mainloop or whatever
            task = self.queue.get()
            do_child_process_start(task)

        def terminate(self):
            do_child_process_stop()

        def reload(self):
            # Done on SIGHUP after the configuration file reloading
            do_child_reload()


    class MyOtherService(cotyledon.Service):
        name = "Second Service"


    class MyThirdService(cotyledon.Service):
        pass


    class MyServiceManager(cotyledon.ServiceManager):
        def __init__(self, conf)
            super(MetricdServiceManager, self).__init__()
            self.conf = conf
            oslo_config_glue.setup(self, self.conf, restart_method='reload')
            self.queue = multiprocessing.Queue()

            # the queue is explicitly passed to this child (it will live
            # on all of them due to the usage of os.fork() to create children)
            sm.add(MyService, workers=2, args=(self.conf, queue))
            self.other_id = sm.add(MyOtherService, workers=conf.other_workers)
            sm.add(MyThirdService, workers=2)

        def run(self):
            do_master_process_start()
            super(MyServiceManager, self).run()
            do_master_process_stop()

        def reload(self):
            # The cotyledon ServiceManager have already reloaded the oslo.config files

            do_master_process_reload()

            # Allow to change the number of worker for MyOtherService
            self.reconfigure(self.other_id, workers=self.conf.other_workers)

    def main():
        conf = cfg.ConfigOpts()
        MyServiceManager(conf).run()


Other examples can be found here:

* :doc:`examples`
* https://github.com/openstack/gnocchi/blob/master/gnocchi/cli.py#L287
* https://github.com/openstack/ceilometer/blob/master/ceilometer/cmd/collector.py

.. _futurist documentation: <http://docs.openstack.org/developer/futurist/api.html#periodics>`
