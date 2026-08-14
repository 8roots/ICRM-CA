# 模型升级与黄金集重跑

PaddleOCR 文本检测/识别与印章检测模型**固定版本 + SHA-256**，镜像构建时下载校验（`backend/scripts/download_models.py`），worker 启动时再校验（`app.paddle_engine.verify_artifacts`）；运行时不下载、不更新模型。

## 模型固定点

- `backend/app/paddle_engine.py` 的 `MODEL_ARTIFACTS`：三个模型的 sha256 与文件名集合。
- `backend/scripts/download_models.py`：下载 URL 前缀（`paddle3.0.0` 官方镜像）与校验逻辑。
- `backend/pyproject.toml` / `backend/uv.lock`：`paddleocr` / `paddlepaddle` 版本。

## 升级流程（任何模型或依赖变更都必须走完）

1. 修改版本/哈希（先以官方发布页的哈希为准，可临时运行 `download_models.py` 验证新工件哈希）。
2. 本地重跑黄金集：
   ```bash
   cd backend
   uv sync --frozen
   uv run python scripts/generate_golden_fixtures.py   # 仅当语料源变化
   uv run python scripts/evaluate_extraction.py        # 原生文本层（确定性）
   ICRM_MODELS_DIR=<模型目录> uv run python scripts/evaluate_extraction.py \
     --engine ocr --report ../docs/release/golden-report-ocr.md
   ```
3. 若任一指标低于阈值（召回率 ≥ 0.90、精确率 ≥ 0.95），**禁止升级**；必要时调整抽取规则并同步更新 `docs/release/golden-report.md` 与 `docs/release/golden-report-ocr.md`。
4. 提交时把两份黄金报告与模型/依赖变更放在同一变更中。
5. 触发发布任务 `release-golden.yml`（模型/依赖路径变更会自动触发）：任务在带固定模型的镜像内重跑 OCR 黄金集，若与已提交报告不一致会失败，直到报告更新——即“依赖/模型变更必须重跑”。

## 验证点

- [ ] `download_models.py` 校验通过（哈希一致）
- [ ] 原生黄金报告与 OCR 黄金报告均达标且已提交
- [ ] 镜像构建成功（构建期即下载校验模型）
- [ ] 发布任务 `release-golden.yml` 通过
