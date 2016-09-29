=====================================
Unix Domain Socket interface examples
=====================================

Examples of command lines via Unix Domain Socket interface::

    # socat - UNIX:./control

    examples.py> list
    service 'control-1' 2 workers
    1. worker(0): 3557
    2. worker(1): 3558
    service 'control-2' 1 workers
    1. worker(0): 3559

    examples.py> worker control-1 +10
    service 'control-1' have now 12 workers

    examples.py> list
    service 'control-1' 12 workers
    1. worker(0): 3557
    2. worker(1): 3558
    3. worker(2): 3625
    4. worker(3): 3626
    5. worker(4): 3627
    6. worker(5): 3630
    7. worker(6): 3632
    8. worker(7): 3634
    9. worker(8): 3638
    10. worker(9): 3639
    11. worker(10): 3643
    12. worker(11): 3645
    service 'control-2' 1 workers
    1. worker(0): 3559

    examples.py> reload
    signal SIGHUP sent to master process

    examples.py> abort
    signal SIGINT sent to master process


