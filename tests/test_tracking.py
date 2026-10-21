from ethtester.tracking import FlapTracker, PlugSession


def test_flap_tracker_counts_cycles_in_window():
    t = FlapTracker(window=60)
    assert t.update(0, 2) == 0
    assert t.update(10, 4) == 1
    assert t.update(20, 6) == 2
    assert t.update(30, 8) == 3
    assert t.update(100, 8) == 0


def test_session_measures_time_to_ip_after_replug():
    s = PlugSession()
    s.on_link(0, False)
    s.on_link(1, True)
    s.on_ip(1, False)
    assert s.fresh
    assert s.seconds_since_link(2) == 1
    s.on_ip(3.5, True)
    assert s.seconds_to_ip == 2.5
    s.on_ip(9, True)
    assert s.seconds_to_ip == 2.5


def test_session_already_up_at_start_is_not_fresh():
    s = PlugSession()
    s.on_link(0, True)
    s.on_ip(0, True)
    assert not s.fresh
    assert s.seconds_to_ip is None


def test_session_resets_on_unplug():
    s = PlugSession()
    s.on_link(0, False)
    s.on_link(1, True)
    s.on_ip(2, True)
    s.on_link(5, False)
    assert s.seconds_since_link(5) is None
    assert s.seconds_to_ip is None
