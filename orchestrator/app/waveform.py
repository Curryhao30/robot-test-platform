"""逐周期波形图生成（P1-6）：纯 Python 产出零依赖 SVG，供报告/面试演示。

RunCycles 返回逐周期 interval_us（实际周期间隔）与 processing_us（单周期
处理耗时）。本模块把它们画成两张折线图：
  - jitter 波形：interval_us - 名义周期，反映调度抖动
  - latency 波形：processing_us，反映单周期交换处理耗时（含 overrun 判断线）

不依赖 matplotlib：CI / 面试机无需额外安装，SVG 可直接浏览器打开。
"""
from __future__ import annotations

import math


def _polyline(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.1f},{y:.2f}" for x, y in points)


def waveform_svg(data: list[float], *, title: str, y_label: str,
                 baseline: float | None = None, limit: float | None = None,
                 width: int = 900, height: int = 240,
                 highlight_above: float | None = None) -> str:
    """把一维序列画成折线 SVG。

    baseline: 名义值参考线（如名义周期间隔 2000us）
    limit:    阈值红线（如 2000us overrun 线）
    highlight_above: 超过该值的点用红色（overrun / 抖动超标）
    """
    n = len(data)
    if n == 0:
        return f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}'><text x='16' y='24' font-size='14'>{title}: 无数据</text></svg>"
    pad_l, pad_r, pad_t, pad_b = 64, 24, 32, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    vals = list(data) + [v for v in (baseline, limit) if v is not None]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    span = hi - lo
    lo -= span * 0.08
    hi += span * 0.08

    def x(i: float) -> float:
        return pad_l + (i / max(1, n - 1)) * plot_w if n > 1 else pad_l + plot_w / 2

    def y(v: float) -> float:
        return pad_t + (1 - (v - lo) / (hi - lo)) * plot_h

    parts: list[str] = []
    parts.append(f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
                 f"font-family='Segoe UI, Microsoft YaHei, sans-serif'>")
    parts.append(f"<text x='{pad_l}' y='20' font-size='15' font-weight='700' fill='#0f172a'>{title}</text>")
    # 网格与 Y 轴刻度（5 档）
    for k in range(6):
        v = lo + (hi - lo) * k / 5
        yy = y(v)
        parts.append(f"<line x1='{pad_l}' y1='{yy:.1f}' x2='{width - pad_r}' y2='{yy:.1f}' "
                     f"stroke='#e2e8f0' stroke-width='1'/>")
        parts.append(f"<text x='{pad_l - 8}' y='{yy + 4:.1f}' font-size='11' fill='#64748b' "
                     f"text-anchor='end'>{v:.0f}</text>")
    parts.append(f"<text x='{pad_l - 8}' y='{pad_t - 8}' font-size='11' fill='#64748b' "
                 f"text-anchor='end'>{y_label}</text>")
    # 参考线 / 阈值线
    if baseline is not None:
        yy = y(baseline)
        parts.append(f"<line x1='{pad_l}' y1='{yy:.1f}' x2='{width - pad_r}' y2='{yy:.1f}' "
                     f"stroke='#0e7490' stroke-width='1.5' stroke-dasharray='6,4'/>")
        parts.append(f"<text x='{width - pad_r}' y='{yy - 6:.1f}' font-size='11' fill='#0e7490' "
                     f"text-anchor='end'>nominal {baseline:.0f}us</text>")
    if limit is not None:
        yy = y(limit)
        parts.append(f"<line x1='{pad_l}' y1='{yy:.1f}' x2='{width - pad_r}' y2='{yy:.1f}' "
                     f"stroke='#dc2626' stroke-width='1.5'/>")
        parts.append(f"<text x='{pad_l}' y='{yy - 6:.1f}' font-size='11' fill='#dc2626'>"
                     f"limit {limit:.0f}us</text>")
    # 折线：整体灰蓝 + 超标段红色
    pts = [(x(i), y(v)) for i, v in enumerate(data)]
    parts.append(f"<polyline points='{_polyline(pts)}' fill='none' stroke='#2563eb' "
                 f"stroke-width='1.6' stroke-linejoin='round'/>")
    if highlight_above is not None:
        seg: list[tuple[float, float]] = []
        for i, v in enumerate(data):
            if v > highlight_above:
                seg.append((x(i), y(v)))
            elif len(seg) >= 2:
                parts.append(f"<polyline points='{_polyline(seg)}' fill='none' stroke='#dc2626' "
                             f"stroke-width='2.2'/>")
                seg = []
        if len(seg) >= 2:
            parts.append(f"<polyline points='{_polyline(seg)}' fill='none' stroke='#dc2626' "
                         f"stroke-width='2.2'/>")
    # X 轴刻度（首/中/尾）
    for i in (0, n // 2, n - 1):
        parts.append(f"<text x='{x(i):.1f}' y='{height - 12}' font-size='11' fill='#64748b' "
                     f"text-anchor='middle'>#{i}</text>")
    parts.append(f"<text x='{width - pad_r}' y='{height - 12}' font-size='11' fill='#94a3b8' "
                 f"text-anchor='end'>cycle index →</text>")
    parts.append("</svg>")
    return "\n".join(parts)


def jitter_waveform(interval_us: list[float], cycle_hz: int) -> str:
    """jitter 波形：实际周期间隔 vs 名义周期。"""
    nominal = 1_000_000.0 / cycle_hz
    jitter = [iv - nominal for iv in interval_us]
    return waveform_svg(
        jitter, title=f"Cycle Jitter @ {cycle_hz} Hz (vs nominal {nominal:.0f}us)",
        y_label="jitter (us)", baseline=0.0)


def latency_waveform(processing_us: list[float], cycle_hz: int) -> str:
    """latency 波形：单周期处理耗时 vs 周期上限（overrun 线）。"""
    limit = 1_000_000.0 / cycle_hz
    return waveform_svg(
        processing_us, title=f"Per-Cycle Processing Latency @ {cycle_hz} Hz",
        y_label="latency (us)", limit=limit, highlight_above=limit)


def stats_line(data: list[float]) -> str:
    if not data:
        return "n/a"
    lo, hi = min(data), max(data)
    mean = sum(data) / len(data)
    var = sum((v - mean) ** 2 for v in data) / len(data)
    return f"min={lo:.1f} max={hi:.1f} mean={mean:.1f} std={math.sqrt(var):.1f} (us)"
