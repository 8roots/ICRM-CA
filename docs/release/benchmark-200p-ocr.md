# 200 页参考工作量基准（benchmark report）

- 命令: `cd backend && uv run python scripts/benchmark_200p.py --engine ocr --runs 3 --pages 200`
- 代码版本: `d4f42ea`
- 机器: DESKTOP-0BVIJHS / Linux 6.18.33.2-microsoft-standard-WSL2 / x86_64 / 8 cores / 15.6 GB RAM
- 抽取引擎: `ocr` — PP-OCRv5_mobile_det=50446e5d01ac, PP-OCRv5_mobile_rec=566b9512b34e, PP-OCRv4_mobile_seal_det=cc245c1b0ab2
- 语料: 200 页合成文本 PDF（`china-s` CJK 字体），417443 字节
- 运行次数: 3
- 耗时（秒）: P95=473.69，中位数=367.46，均值=399.91，最小=358.59，最大=473.69
- 完成: 解析页面 200/200
- 结论: P95 ≤ 600s（10 分钟）→ 是
