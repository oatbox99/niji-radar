from src.dam import latest_slope, parse_dam_rows

# newest-first; cols = [date, time, 雨量, 貯水量, 流入量, 放流量, 貯水率]
IFRAME = """<table>
<tr><th>年月日</th><th>時刻</th><th>雨量</th><th>貯水量</th><th>流入量</th><th>放流量</th><th>貯水率</th></tr>
<tr><td>2026/07/18</td><td>10:10</td><td>0.0</td><td>83772</td><td>6.2</td><td>40.0</td><td>72.5</td></tr>
<tr><td>2026/07/18</td><td>10:00</td><td>0.0</td><td>83770</td><td>6.0</td><td>38.0</td><td>72.5</td></tr>
<tr><td>2026/07/18</td><td>09:50</td><td>0.0</td><td>83768</td><td>6.0</td><td>36.0</td><td>72.4</td></tr>
<tr><td>2026/07/18</td><td>09:40</td><td>0.0</td><td>83766</td><td>6.0</td><td>30.0</td><td>72.4</td></tr>
<tr><td>2026/07/18</td><td>09:30</td><td>0.0</td><td>83764</td><td>6.0</td><td>28.0</td><td>72.3</td></tr>
<tr><td>2026/07/18</td><td>09:20</td><td>0.0</td><td>83762</td><td>6.0</td><td>24.0</td><td>72.3</td></tr>
<tr><td>2026/07/18</td><td>09:10</td><td>0.0</td><td>83760</td><td>6.0</td><td>20.0</td><td>72.2</td></tr>
</table>"""


def test_parse_dam_rows_extracts_discharge_and_rain():
    rows = parse_dam_rows(IFRAME)
    assert len(rows) == 7                      # header (th) row skipped
    assert rows[0] == ("2026/07/18 10:10", 0.0, 40.0)   # (datetime, rainfall, discharge)


def test_latest_slope_computes_hourly_surge():
    s = latest_slope(parse_dam_rows(IFRAME))
    assert s["discharge"] == 40.0
    assert abs(s["slope_pct"] - 1.0) < 1e-9    # (40-20)/20 = +100%


def test_latest_slope_from_zero_above_floor_is_sentinel():
    rows = [("t0", 0.0, 50.0)] + [(f"t{i}", 0.0, 0.0) for i in range(1, 7)]
    assert latest_slope(rows)["slope_pct"] == 999.0


def test_latest_slope_from_zero_below_floor_is_noise():
    rows = [("t0", 0.0, 5.0)] + [(f"t{i}", 0.0, 0.0) for i in range(1, 7)]
    assert latest_slope(rows)["slope_pct"] == 0.0


def test_latest_slope_empty_is_none():
    assert latest_slope([])["slope_pct"] is None


def test_latest_slope_skips_missing_cells():
    # newest bin is 欠測 (None discharge); slope must still compute from valid neighbors
    rows = [("t0", 0.0, None), ("t1", 0.0, 60.0), ("t2", 0.0, 58.0), ("t3", 0.0, 40.0),
            ("t4", 0.0, 30.0), ("t5", 0.0, 24.0), ("t6", 0.0, 22.0), ("t7", 0.0, 20.0)]
    s = latest_slope(rows)
    assert s["discharge"] == 60.0                       # None top row skipped
    assert abs(s["slope_pct"] - (60.0 - 22.0) / 22.0) < 1e-9   # prev = first valid >= 1h back


def test_latest_slope_short_series_is_unknown_not_fabricated():
    rows = [("t0", 0.0, 50.0), ("t1", 0.0, 45.0), ("t2", 0.0, 40.0)]   # < 1h of data
    assert latest_slope(rows)["slope_pct"] is None      # no ~1h-ago row → unknown, not 0
