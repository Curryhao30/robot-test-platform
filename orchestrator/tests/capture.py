"""测试运行期共享状态：详情收集 + 轨迹捕获 + 逐周期波形（供报告生成使用）。"""
from __future__ import annotations

DETAILS: dict[str, str] = {}
TRAJECTORY = None  # (t_ns, q, dq, ddq)，由精度用例写入
WAVEFORMS: list[dict] = []  # 逐周期 jitter/latency 数据，由 test_waveform 写入，write_run 落盘
