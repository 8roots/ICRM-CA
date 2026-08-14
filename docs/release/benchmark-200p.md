# 200 页参考工作量基准（benchmark report）

- 命令: `cd backend && uv run python scripts/benchmark_200p.py --engine native --runs 10 --pages 200`
- 代码版本: `d4f42ea`
- 机器: DESKTOP-0BVIJHS / Linux 6.18.33.2-microsoft-standard-WSL2 / x86_64 / 8 cores / 15.6 GB RAM
- 抽取引擎: `native` — none (native text layer)
- 语料: 200 页合成文本 PDF（`china-s` CJK 字体），417443 字节
- 运行次数: 10
- 耗时（秒）: P95=4.02，中位数=3.93，均值=3.92，最小=3.83，最大=4.02
- 完成: 解析页面 200/200
- 结论: P95 ≤ 600s（10 分钟）→ 是
