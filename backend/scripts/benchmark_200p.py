"""200-page reference workload benchmark with repeatable P95 measurement.

Generates a 200-page synthetic text PDF (CJK, ``china-s`` font) and runs the
machine-processing parse pipeline over it repeatedly, reporting wall-clock
P95/latest/mean per the release ticket (P95 <= 10 minutes on the reference
8-core/32-GB host). The result is written to ``docs/release/benchmark-200p.md``
so command, machine data, and outcome are recorded with the evidence.

``--engine native`` (default) measures native text extraction plus the 2x
page rendering the worker always performs; it is deterministic and runs in CI.
``--engine ocr`` additionally drives the pinned PaddleOCR seal-detection
pipeline per page and requires ``ICRM_MODELS_DIR`` with verified artifacts —
that is the reference-host measurement.

Usage:
    cd backend && uv run python scripts/benchmark_200p.py [--engine native|ocr] [--runs 10]
"""

import argparse
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pymupdf

from app.parsing import parse_material

REPORT = Path(__file__).resolve().parents[2] / "docs" / "release" / "benchmark-200p.md"
P95_BUDGET_SECONDS = 600  # 10 minutes

FILLER_LINES = (
    "企业名称：参考企业有限公司",
    "统一社会信用代码：91330100MA27XW0001",
    "法定代表人：参考法人",
    "贷款金额：500万元",
    "贷款期限：12个月",
    "年利率：4.0%",
    "还款方式：等额本息",
    "计息方式：按月计息",
    "本页为合成参考工作量，仅用于性能测量，不代表任何真实申请。",
)


def generate_reference_pdf(destination: Path, pages: int = 200) -> None:
    pdf = pymupdf.open()
    fontsize = 11
    line_height = 15
    margin = 56
    for page_index in range(pages):
        page = pdf.new_page(width=595, height=842)
        y = margin
        lines = [f"参考工作量 第 {page_index + 1} 页 / 共 {pages} 页"] + list(FILLER_LINES)
        for line in lines:
            page.insert_text((margin, y), line, fontname="china-s", fontsize=fontsize)
            y += line_height
    pdf.save(str(destination))
    pdf.close()


def machine_description() -> str:
    memory_gb = 0
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        memory_gb = round(int(line.split()[1]) / 1024 / 1024, 1)
                        break
    except OSError:
        pass
    return (
        f"{platform.node()} / {platform.system()} {platform.release()} / "
        f"{platform.machine()} / {os.cpu_count() or '?'} cores / "
        f"{memory_gb or '?'} GB RAM"
    )


def git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        choices=("native", "ocr"),
        default="native",
        help="native = text layer + 2x render (CI); ocr = plus pinned PaddleOCR models",
    )
    parser.add_argument("--runs", type=int, default=10, help="measurement runs (default 10)")
    parser.add_argument("--pages", type=int, default=200, help="pages in the synthetic PDF")
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)

    if args.engine == "ocr":
        from app.config import settings
        from app.paddle_engine import PaddleEngine

        engine = PaddleEngine(settings.models_dir, cpu_threads=os.cpu_count() or 1)
    else:
        from scripts.evaluate_extraction import NativeTextEngine

        engine = NativeTextEngine()

    material = Path("/tmp/icrm-reference-200p.pdf")
    generate_reference_pdf(material, args.pages)
    size_bytes = material.stat().st_size

    times: list[float] = []
    pages_ok = 0
    for _ in range(args.runs):
        started = time.perf_counter()
        with material.open("rb") as stream:
            parsed = parse_material(material.name, stream, engine)
        elapsed = time.perf_counter() - started
        times.append(elapsed)
        pages_ok = max(pages_ok, len(parsed.pages))

    sorted_times = sorted(times)
    p95 = sorted_times[min(int(0.95 * len(sorted_times)), len(sorted_times) - 1)]
    median = statistics.median(times)
    mean = statistics.mean(times)
    ok = p95 <= P95_BUDGET_SECONDS and pages_ok == args.pages

    model_note = "none (native text layer)"
    if args.engine == "ocr":
        from app.paddle_engine import MODEL_ARTIFACTS

        model_note = ", ".join(f"{a.name}={a.sha256[:12]}" for a in MODEL_ARTIFACTS)

    report = "\n".join(
        [
            "# 200 页参考工作量基准（benchmark report）",
            "",
            f"- 命令: `cd backend && uv run python scripts/benchmark_200p.py "
            f"--engine {args.engine} --runs {args.runs} --pages {args.pages}`",
            f"- 代码版本: `{git_revision()}`",
            f"- 机器: {machine_description()}",
            f"- 抽取引擎: `{args.engine}` — {model_note}",
            f"- 语料: {args.pages} 页合成文本 PDF（`china-s` CJK 字体），{size_bytes} 字节",
            f"- 运行次数: {args.runs}",
            f"- 耗时（秒）: P95={p95:.2f}，中位数={median:.2f}，均值={mean:.2f}，"
            f"最小={min(times):.2f}，最大={max(times):.2f}",
            f"- 完成: 解析页面 {pages_ok}/{args.pages}",
            f"- 结论: P95 ≤ {P95_BUDGET_SECONDS}s（10 分钟）→ " + ("是" if ok else "否"),
            "",
        ]
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(f"P95={p95:.2f}s median={median:.2f}s mean={mean:.2f}s pages={pages_ok}/{args.pages}")
    print(f"report written to {args.report}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
