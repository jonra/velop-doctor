from ethtester.tracking import FlapTracker, PlugSession


def test_flap_tracker_counts_cycles_in_window():
    t = FlapTracker(window=60)
    assert t.update(0, 2) == 0
    assert t.update(10, 4) == 1
    assert t.update(20, 6) == 2
    assert t.update(30, 8) == 3
    assert t.update(100, 8) == 0


def test_flap_tracker_resets_window_when_counter_decreases():
    t = FlapTracker(window=60)
    assert t.update(0, 20) == 0
    assert t.update(10, 24) == 2
    assert t.update(20, 1) == 0
    assert t.update(30, 3) == 1


def test_session_measures_time_to_ip_after_replug():
    s = PlugSession()
    assert s.on_link(0, False, 1) is False
    assert s.on_link(1, True, 2) is True
    s.on_ip(1, False)
    assert s.fresh
    assert s.seconds_since_link(2) == 1
    s.on_ip(3.5, True)
    assert s.seconds_to_ip == 2.5
    s.on_ip(9, True)
    assert s.seconds_to_ip == 2.5


def test_session_already_up_at_start_is_not_fresh():
    s = PlugSession()
    assert s.on_link(0, True, 2) is False
    s.on_ip(0, True)
    assert not s.fresh
    assert s.on_link(5, True, 2) is False
    assert s.seconds_to_ip is None


def test_session_resets_on_unplug():
    s = PlugSession()
    s.on_link(0, False, 1)
    s.on_link(1, True, 2)
    s.on_ip(2, True)
    s.on_link(5, False, 3)
    assert s.seconds_since_link(5) is None
    assert s.seconds_to_ip is None


def test_session_infers_replug_from_carrier_changes():
    s = PlugSession()
    s.on_link(0, True, 2)
    s.on_ip(0, True)
    assert s.on_link(10, True, 4) is True
    assert s.fresh
    assert s.seconds_since_link(10) == 0
    assert s.seconds_to_ip is None
    s.on_ip(12, True)
    assert s.seconds_to_ip == 2
    assert s.on_link(13, True, 4) is False
