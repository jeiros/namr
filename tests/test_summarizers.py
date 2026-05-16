from namr.pipeline import _summarize_laps, _summarize_segments


def test_laps_none_when_no_laps():
    assert _summarize_laps({}, "Run") is None
    assert _summarize_laps({"laps": []}, "Run") is None


def test_laps_none_when_single_lap():
    # A single "lap" === whole-activity; not useful.
    assert _summarize_laps({"laps": [{"distance": 1000, "moving_time": 300}]}, "Run") is None


def test_laps_run_uses_pace():
    raw = {
        "laps": [
            {"lap_index": 1, "distance": 1000, "moving_time": 330, "average_heartrate": 145.2},
            {"lap_index": 2, "distance": 1000, "moving_time": 320, "average_heartrate": 152.7},
            {"lap_index": 3, "distance": 1000, "moving_time": 310, "average_heartrate": 158.0},
        ]
    }
    s = _summarize_laps(raw, "Run")
    assert s is not None
    assert "laps (3)" in s
    assert "5:30/km" in s
    assert "145 bpm" in s
    # Negative split shape preserved (paces decreasing)
    assert s.index("5:30/km") < s.index("5:20/km") < s.index("5:10/km")


def test_laps_ride_uses_speed():
    raw = {
        "laps": [
            {"lap_index": 1, "distance": 5000, "moving_time": 600, "average_speed": 8.33},
            {"lap_index": 2, "distance": 5000, "moving_time": 580, "average_speed": 8.62},
        ]
    }
    s = _summarize_laps(raw, "Ride")
    assert s is not None
    assert "km/h" in s
    assert "5:30/km" not in s


def test_laps_truncated_with_note():
    raw = {"laps": [{"lap_index": i + 1, "distance": 400, "moving_time": 90} for i in range(15)]}
    s = _summarize_laps(raw, "Run", max_laps=10)
    assert s is not None
    assert "first 10 shown" in s
    assert s.count("\n  ") == 10  # 10 lap rows


def test_segments_none_when_empty():
    assert _summarize_segments({}) is None
    assert _summarize_segments({"segment_efforts": []}) is None


def test_segments_prs_first_then_koms():
    raw = {
        "segment_efforts": [
            {"segment": {"name": "Long Drag", "distance": 2400, "average_grade": 3.1},
             "elapsed_time": 420},
            {"segment": {"name": "Tibidabo", "distance": 5200, "average_grade": 7.8},
             "elapsed_time": 1530, "kom_rank": 5},
            {"segment": {"name": "Final Sprint", "distance": 200, "average_grade": -1.0},
             "elapsed_time": 18, "pr_rank": 2},
        ]
    }
    s = _summarize_segments(raw)
    assert s is not None
    # PR is ranked first, then KOM, then plain
    assert s.index("Final Sprint") < s.index("Tibidabo") < s.index("Long Drag")
    assert "PR #2" in s
    assert "KOM #5" in s
    assert "+7.8%" in s  # signed grade
