from dsbench.doctor import MIN_FREE_DISK_GIB, MIN_RAM_GIB, capacity_ok


def test_capacity_accepts_exact_two_slot_minimum():
    assert MIN_RAM_GIB == 16
    assert capacity_ok(4, 16 * 1024**3, MIN_FREE_DISK_GIB * 1024**3)


def test_capacity_rejects_each_resource_below_minimum():
    enough_ram = MIN_RAM_GIB * 1024**3
    enough_disk = MIN_FREE_DISK_GIB * 1024**3
    assert not capacity_ok(3.99, enough_ram, enough_disk)
    assert not capacity_ok(4, enough_ram - 1, enough_disk)
    assert not capacity_ok(4, enough_ram, enough_disk - 1)
