import math

import numpy as np

from flightoverlay.telemetry.series import STALE_AFTER, Series


def test_linear_interpolation():
    s = Series([0, 1, 2], [0, 10, 20])
    assert s.at(0.5) == 5
    assert s.at(2.0) == 20


def test_outside_range_is_nan_but_holds_briefly():
    s = Series([10, 11], [1, 2])
    assert math.isnan(s.at(9.9))
    assert s.at(11 + STALE_AFTER / 2) == 2
    assert math.isnan(s.at(11 + STALE_AFTER + 0.1))


def test_hold_keeps_last_value():
    s = Series([0, 5], [1, 7], "hold")
    assert s.at(4.99) == 1
    assert s.at(5.0) == 7


def test_heading_interpolates_across_north():
    s = Series([0, 1], [350, 10], "angle360")
    assert abs(s.at(0.5) - 0.0) < 1e-6 or abs(s.at(0.5) - 360.0) < 1e-6
    assert abs(s.at(0.25) - 355.0) < 1e-6


def test_roll_interpolates_across_180():
    s = Series([0, 1], [170, -170], "angle180")
    assert abs(abs(s.at(0.5)) - 180.0) < 1e-6


def test_at_many_matches_at():
    t = np.linspace(0, 10, 50)
    s = Series(t, np.sin(t))
    xs = np.array([-1, 0.3, 5.5, 9.99, 20])
    many = s.at_many(xs)
    for x, v in zip(xs, many):
        single = s.at(x)
        assert (math.isnan(v) and math.isnan(single)) or abs(v - single) < 1e-12


def test_unsorted_input_is_sorted_and_nans_dropped():
    s = Series([2, 0, 1, 3], [20, 0, 10, float("nan")])
    assert list(s.t) == [0, 1, 2]
    assert s.at(1.5) == 15
