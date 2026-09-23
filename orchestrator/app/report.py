"""测试报告生成：reports/run_YYYYMMDD_NNN/{result.json,trajectory.csv,report.html,log.txt}

供面试演示使用：一条命令跑完，生成可打开的 HTML 报告。
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import pathlib
from dataclasses import dataclass, field

import numpy as np


@dataclass
class RunRecord:
    name: str
    status: str          # PASS / FAIL / ERROR
    detail: str = ""
    duration_s: float = 0.0


@dataclass
class RunContext:
    profile_name: str
    profile_model: str
    controller: str
    cycle_hz: int
    port: int
    started_at: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))
    records: list[RunRecord] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", duration_s: float = 0.0):
        self.records.append(RunRecord(name, status, detail, duration_s))

    @property
    def passed(self) -> int:
        return sum(1 for r in self.records if r.status == "PASS")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.records if r.status != "PASS")


def next_run_dir(reports_root: pathlib.Path) -> pathlib.Path:
    reports_root.mkdir(parents=True, exist_ok=True)
    today = _dt.datetime.now().strftime("%Y%m%d")
    seq = 1
    while (reports_root / f"run_{today}_{seq:03d}").exists():
        seq += 1
    d = reports_root / f"run_{today}_{seq:03d}"
    d.mkdir(parents=True)
    return d


def write_run(ctx: RunContext, run_dir: pathlib.Path, trajectory=None,
               waveforms=None):
    """写出 result.json / trajectory.csv / report.html / log.txt / waveform.json。

    waveform.json：逐周期 jitter/latency 序列（源自 test_waveform），供控制台
    曲线可视化；无波形数据时跳过写盘。
    """
    # result.json
    result = {
        "profile": {"name": ctx.profile_name, "model": ctx.profile_model},
        "controller": ctx.controller,
        "cycle_hz": ctx.cycle_hz,
        "port": ctx.port,
        "started_at": ctx.started_at,
        "summary": {"passed": ctx.passed, "failed": ctx.failed,
                    "total": len(ctx.records)},
        "cases": [r.__dict__ for r in ctx.records],
    }
    (run_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # waveform.json（逐周期曲线数据源，对应"数据曲线可视化"）
    if waveforms:
        wf = [{
            "name": w.get("name", ""),
            "cycle_hz": w.get("cycle_hz", 0),
            "slaves": w.get("slaves", ""),
            "interval_us": [float(x) for x in w.get("interval_us", [])],
            "processing_us": [float(x) for x in w.get("processing_us", [])],
            "jitter_stats": w.get("jitter_stats", ""),
            "latency_stats": w.get("latency_stats", ""),
            "overrun_cycles": int(w.get("overrun_cycles", 0)),
        } for w in waveforms]
        (run_dir / "waveform.json").write_text(
            json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")

    # trajectory.csv（若有轨迹数据：来自 MoveAbsolute 精度用例）
    if trajectory is not None:
        t_ns, q, dq, ddq = trajectory
        with open(run_dir / "trajectory.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            header = ["timestamp_ns"]
            header += [f"q{i}" for i in range(q.shape[0])]
            header += [f"dq{i}" for i in range(q.shape[0])]
            header += [f"ddq{i}" for i in range(q.shape[0])]
            writer.writerow(header)
            for i in range(q.shape[1]):
                row = [int(t_ns[i])]
                row += [f"{q[a, i]:.6f}" for a in range(q.shape[0])]
                row += [f"{dq[a, i]:.6f}" for a in range(q.shape[0])]
                row += [f"{ddq[a, i]:.6f}" for a in range(q.shape[0])]
                writer.writerow(row)

    # report.html（可直接浏览器打开）
    rows = "\n".join(
        f'<tr><td class="{r.status.lower()}">{r.status}</td>'
        f"<td>{_esc(r.name)}</td><td>{_esc(r.detail)}</td>"
        f"<td>{r.duration_s:.2f}s</td></tr>"
        for r in ctx.records
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Robot Controller Test Report</title>
<style>
body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; margin: 24px; background: #f8fafc; color: #0f172a; }}
pre.banner {{ background: #0f172a; color: #a5f3fc; padding: 16px; border-radius: 8px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
th, td {{ border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left; }}
th {{ background: #0e7490; color: #fff; }}
.pass {{ color: #15803d; font-weight: 700; }} .fail {{ color: #b91c1c; font-weight: 700; }} .error {{ color: #b91c1c; font-weight: 700; }}
.summary {{ margin-top: 12px; font-size: 14px; }}
</style></head><body>
<h2>Robot Controller Test Report</h2>
<pre class="banner">================ Robot Controller Test ================
Profile       : {_esc(ctx.profile_name)} ({_esc(ctx.profile_model)})
Controller    : {_esc(ctx.controller)}
Cycle         : {ctx.cycle_hz} Hz
{_banner_lines(ctx.records)}
{ctx.passed} passed, {ctx.failed} failed</pre>
<table><tr><th>结果</th><th>用例</th><th>详情</th><th>耗时</th></tr>{rows}</table>
<div class="summary">开始时间: {_esc(ctx.started_at)} | 端口: {ctx.port} | 轨迹: trajectory.csv</div>
</body></html>"""
    (run_dir / "report.html").write_text(html, encoding="utf-8")

    # log.txt（测试结果文本）
    lines = [f"Profile: {ctx.profile_name} | Model: {ctx.profile_model}",
             f"Controller: {ctx.controller} | Cycle: {ctx.cycle_hz} Hz",
             f"Started: {ctx.started_at}"]
    for r in ctx.records:
        lines.append(f"[{r.status}] {r.name} ({r.duration_s:.2f}s) {r.detail}")
    lines.append(f"Summary: {ctx.passed} passed, {ctx.failed} failed")
    (run_dir / "log.txt").write_text("\n".join(lines), encoding="utf-8")

    return run_dir


def _banner_lines(records) -> str:
    out = []
    for r in records:
        out.append(f"[{r.status}] {r.name}")
    return "\n".join(out)


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
