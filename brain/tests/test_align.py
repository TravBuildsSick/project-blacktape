from blacktape_brain.align import parse_timestamp, to_local_time

def test_parse_timestamp_falsy_defaults_to_epoch():
    assert parse_timestamp(None) == "1970-01-01 00:00:00"
    assert parse_timestamp("") == "1970-01-01 00:00:00"
    assert parse_timestamp(0) == "1970-01-01 00:00:00"

def test_parse_timestamp_handles_datetime_object():
    from datetime import datetime

    assert parse_timestamp(datetime(2024, 5, 1, 18, 0, 0)) == "2024-05-01 18:00:00"

def test_parse_timestamp_handles_known_string_formats():
    assert parse_timestamp("2024-05-01 18:00:00") == "2024-05-01 18:00:00"
    assert parse_timestamp("2024-05-01T18:00:00Z") == "2024-05-01 18:00:00"
    assert parse_timestamp("05/01/2024 06:00 PM") == "2024-05-01 18:00:00"

def test_parse_timestamp_converts_epoch_int_to_chronological_string():

    assert parse_timestamp(1714586700) == "2024-05-01 18:05:00"

def test_parse_timestamp_converts_epoch_float_and_numeric_string():
    assert parse_timestamp(1714586700.0) == "2024-05-01 18:05:00"
    assert parse_timestamp("1714586700") == "2024-05-01 18:05:00"

def test_parse_timestamp_converts_epoch_milliseconds_to_chronological_string():

    assert parse_timestamp(1714586700000) == "2024-05-01 18:05:00"
    assert parse_timestamp(1714586700000.0) == "2024-05-01 18:05:00"
    assert parse_timestamp("1714586700000") == "2024-05-01 18:05:00"

def test_parse_timestamp_falls_back_to_str_for_unrecognized_value():

    assert parse_timestamp("not a real timestamp") == "not a real timestamp"
    assert parse_timestamp(["nonsense"]) == "['nonsense']"

def test_parse_timestamp_falls_back_to_str_for_out_of_range_epoch_value():

    out_of_range = 999999999999999999
    assert parse_timestamp(out_of_range) == str(out_of_range)
    assert parse_timestamp(float(out_of_range)) == str(float(out_of_range))
    assert parse_timestamp(str(out_of_range)) == str(out_of_range)

def test_to_local_time_converts_utc_into_named_zone():

    assert to_local_time("2024-05-01 18:00:00", tz="America/New_York") == "2024-05-01 14:00:00"

def test_to_local_time_resolves_dst_per_instant_not_now():

    assert to_local_time("2024-01-15 12:00:00", tz="America/New_York") == "2024-01-15 07:00:00"
    assert to_local_time("2024-07-15 12:00:00", tz="America/New_York") == "2024-07-15 08:00:00"

def test_to_local_time_can_cross_the_day_boundary():
    assert to_local_time("2024-06-01 02:00:00", tz="America/New_York") == "2024-05-31 22:00:00"

def test_to_local_time_defaults_to_utc_noop_when_tz_is_utc():
    assert to_local_time("2024-05-01 18:00:00", tz="UTC") == "2024-05-01 18:00:00"

def test_to_local_time_leaves_unparseable_values_unchanged():
    assert to_local_time("not a real timestamp", tz="America/New_York") == "not a real timestamp"

def test_to_local_time_converts_the_epoch_default_like_any_other_instant():

    assert to_local_time(None, tz="America/New_York") == "1969-12-31 19:00:00"
