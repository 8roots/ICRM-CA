# 发布检查（release check）

发布 = 走完本清单并让 CI/发布任务全绿。任何一项失败都不得发布。

## 1. CI（`.github/workflows/ci.yml`）

- [ ] backend：`ruff check` + `pytest`（含解析契约、规则、黄金集、生产 Compose 姿态测试）
- [ ] frontend：`vue-tsc` 类型检查 + Vitest + 生产构建
- [ ] OpenAPI 漂移：`backend/openapi.json` 与 `frontend/src/api/generated.ts` 重新生成后 `git diff` 为空
- [ ] 黄金报告漂移：`evaluate_extraction.py --check-drift` 通过（`docs/release/golden-report.md` 为最新）
- [ ] Compose 冒烟：`docker compose up --build` 后 `/health/ready` 返回 ready
- [ ] 端到端：Playwright 全部通过（登录 → 上传 → 候选确认 → 完备性 → 红线 → 辅助审查完成；含企业/个人演示流程与可访问性/布局检查）
- [ ] 漏洞扫描：后端 `pip-audit`、前端 `npm audit --audit-level=high` 无高危及以上

## 2. 发布黄金集（`.github/workflows/release-golden.yml`）

- [ ] 带固定 PaddleOCR 模型的镜像构建成功（构建期哈希校验）
- [ ] OCR 黄金集（`--engine ocr`）与已提交 `docs/release/golden-report-ocr.md` 一致；模型/依赖变更必须同步更新报告
- [ ] 阈值：召回率 ≥ 0.90、精确率 ≥ 0.95；红线关键输入 100% 找到或明确缺失

## 3. 工程完成报告（`docs/release/engineering-complete.md`）

- [ ] 每一项验收均有证据（文件、命令、结果）可追溯
- [ ] 试点就绪门禁（真实脱敏材料、机构批准、供应商协议、基础设施加密/保留、生产级恢复演练、≤15 分钟真实试点）与工程完成明确分离，未满足的不作“生产可用”宣称

## 4. 部署前人工确认

- [ ] `secrets/*` 已按生产填写且权限收紧；无默认凭据
- [ ] TLS 证书有效且续期机制就绪
- [ ] 数据卷与备份加密、保留制度已落实（机构责任）
- [ ] 备份已执行且 `--verify` 通过；演练恢复计划已知
- [ ] 发布主机符合参考规格（8 核 / 32 GB，见基准报告）
