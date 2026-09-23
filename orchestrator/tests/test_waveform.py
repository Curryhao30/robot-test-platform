"""P1-6 用例：RunCycles 逐周期数据 + jitter/latency 波形生成（零依赖 SVG）。

验证：逐周期 interval_us / processing_us 数组长度与统计一致性；
      波形 SVG 可生成、可写盘、浏览器可直接打开（面试演示产物）。
"""
from __future__ import annotations

import pathlib

from tests import capture
from tests.test_ethercat import _out, CW

REPO = pathlib.Path(__file__).resolve().parents[2]


def _waveform_test(client, cycle_hz: int, cycles: int, all_slaves: bool):
    from app.waveform import jitter_waveform, latency_waveform, stats_line

    outputs = [_out(i, CW["enable"]) for i in range(7)] if all_slaves else [_out(0, CW["enable"])]
    r = client.run_cycles(outputs, cycles=cycles, cycle_hz=cycle_hz)
    assert r.ok, r
    # 逐周期明细存在且长度正确
    assert len(r.interval_us) == cycles - 1, (len(r.interval_us), cycles)
    assert len(r.processing_us) == cycles, (len(r.processing_us), cycles)
    # 统计与明细自洽：统计量由明细重算
    assert abs(max(r.interval_us) - r.jitter_max_us - 1_000_000.0 / cycle_hz) < 1.0, (
        max(r.interval_us), r.jitter_max_us)
    # 波形 SVG
    j_svg = jitter_waveform(list(r.interval_us), cycle_hz)
    l_svg = latency_waveform(list(r.processing_us), cycle_hz)
    assert "<svg" in j_svg and "<polyline" in j_svg, j_svg[:120]
    assert "<svg" in l_svg and "<polyline" in l_svg, l_svg[:120]
    # 写盘（面试演示产物）
    out_dir = REPO / "reports" / "waveforms"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"jitter_{cycle_hz}hz.svg").write_text(j_svg, encoding="utf-8")
    (out_dir / f"latency_{cycle_hz}hz.svg").write_text(l_svg, encoding="utf-8")
    # 逐周期序列随本 run 落盘（控制台曲线可视化数据源）
    capture.WAVEFORMS.append({
        "name": "test_waveform_%dhz_%s" % (cycle_hz, "all" if all_slaves else "single"),
        "cycle_hz": cycle_hz,
        "slaves": "all" if all_slaves else "single",
        "interval_us": [float(x) for x in r.interval_us],
        "processing_us": [float(x) for x in r.processing_us],
        "jitter_stats": stats_line(list(r.interval_us)),
        "latency_stats": stats_line(list(r.processing_us)),
        "overrun_cycles": int(r.overrun_cycles),
    })
    capture.DETAILS["test_waveform_500hz"] = (
        f"jitter {stats_line(list(r.interval_us))}; "
        f"latency {stats_line(list(r.processing_us))}; "
        f"overrun={r.overrun_cycles}; SVG -> reports/waveforms/")
    return r


def test_waveform_500hz_single_slave(client):
    r = _waveform_test(client, cycle_hz=500, cycles=100, all_slaves=False)
    assert r.overrun_cycles == 0, r


def test_waveform_1000hz_all_slaves(client):
    r = _waveform_test(client, cycle_hz=1000, cycles=100, all_slaves=True)
    assert r.overrun_cycles == 0, r
