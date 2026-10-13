"""Data Integrity Agent rules, one small series per rule."""

from mahros.integrity import check_series, summarise

WEEKS = [f"w{i}" for i in range(12)]


def rules(beds, used, icu=None, icu_used=None):
    n = len(beds)
    return {f.rule for f in check_series("H", WEEKS[:n], beds, used,
                                         icu or [10] * n, icu_used or [5] * n)}


def test_clean_series_has_no_flags():
    assert rules([100] * 8, [80, 81, 79, 82, 80, 78, 83, 81]) == set()


def test_more_patients_than_beds_is_red():
    assert "patients_exceed_beds" in rules([100] * 3, [80, 130, 80])


def test_more_icu_patients_than_icu_beds_is_red():
    assert "icu_patients_exceed_icu_beds" in rules(
        [100] * 3, [80] * 3, icu=[10] * 3, icu_used=[5, 14, 5])


def test_zero_patients_in_a_staffed_hospital_is_red():
    assert "zero_patients_in_staffed_hospital" in rules([100] * 3, [80, 0, 80])


def test_zero_is_not_flagged_for_a_hospital_that_never_reports_patients():
    assert "zero_patients_in_staffed_hospital" not in rules([100] * 3, [0, 0, 0])


def test_frozen_count_is_amber():
    assert "frozen_count" in rules([100] * 6, [80, 81, 77, 77, 77, 77])


def test_three_repeats_are_not_yet_frozen():
    assert "frozen_count" not in rules([100] * 5, [80, 81, 77, 77, 77])


def test_one_week_spike_is_amber():
    assert "one_week_spike" in rules([100] * 5, [60, 61, 98, 60, 61])


def test_temporary_bed_collapse_is_red_but_a_lasting_change_is_not():
    dip = [500] * 6 + [100] + [500] * 5
    assert "bed_count_collapse" in rules(dip, [300] * 6 + [60] + [300] * 5)
    lasting = [500] * 6 + [250] * 6
    assert "bed_count_collapse" not in rules(lasting, [300] * 6 + [150] * 6)


def test_summary_counts():
    flags = check_series("H", WEEKS[:3], [100] * 3, [80, 130, 80], [10] * 3, [5] * 3)
    s = summarise(flags, 3)
    assert s["red"] == 1 and s["hospitals_with_red"] == 1
