# 工程完成报告（Engineering-complete report）

对应 issue #10（MVP 9/9：发布加固、CI、备份恢复与工程验收）。本文档逐项给出证据（文件/命令/结果）；**未满足项明确列为试点就绪门禁，不构成工程完成或生产可用声明**。

## 术语

- **工程完成**：使用合成与公开材料达到约定功能、测试与性能标准的交付状态（见 CONTEXT.md）。
- **试点就绪**：真实脱敏材料、机构规则/模板、模型供应商协议及基础设施安全门禁均通过验证的状态。

## 1. CI（必检项从全新克隆可复现）

| 验收 | 状态 | 证据 |
| --- | --- | --- |
| CI 检查从全新克隆通过，无需隐藏本地状态或生产凭据 | ✅ | `.github/workflows/ci.yml`；端到端账号由 `backend/scripts/seed_e2e.py` 用当次运行生成的凭据创建 |
| 后端格式化/静态/单元/集成测试 | ✅ | `ruff format --check` + `ruff check` + `uv run pytest`（282 项通过，含 testcontainers 集成） |
| 前端格式化/类型/Vitest/构建 | ✅ | `vue-tsc` + `vitest`（49 项）+ `npm run build` |
| OpenAPI 生成类型漂移 | ✅ | `openapi-drift` 任务：重新导出 OpenAPI 与生成类型后 `git diff --exit-code` 为空 |
| Compose 端到端冒烟 | ✅ | `compose-smoke` 任务：`docker compose up --build` → `/health/ready` → 默认凭据被拒 |
| 镜像构建 | ✅ | 后端/前端 Dockerfile；`compose-smoke` 与 `e2e` 均构建镜像 |
| 依赖漏洞扫描 | ✅ | `pip-audit`（锁定依赖）+ `npm audit --audit-level=high` |

## 2. 端到端演示流（Playwright）

| 验收 | 状态 | 证据 |
| --- | --- | --- |
| 企业演示流（登录→上传→候选确认→完备性→红线→辅助审查完成） | ✅ | `frontend/e2e/demo-corporate.spec.ts`，11/11 用例通过 |
| 个人演示流（同上） | ✅ | `frontend/e2e/demo-individual.spec.ts` |
| 既有矩阵（上传/证据/完备性/红线/管理员） | ✅ | `frontend/e2e/*`（upload-worker、completeness、redline） |
| 浏览器/布局/可访问性 | ✅ | `frontend/e2e/ui-accessibility.spec.ts`（1366/1920 无横向溢出、zh-CN、键盘可达、非纯颜色状态） |

> 修复了既有用例暴露的真实缺陷：详情页状态在“标记完成”后未刷新（`ApplicationDetailView.vue` 现在随 lifecycle 同步）；以及若干测试定位问题（el-select 占位层拦截指针点击、按钮可访问名等）。

## 3. 黄金集与抽取指标

| 验收 | 状态 | 证据 |
| --- | --- | --- |
| 全支持格式的有标签语料 | ✅ | `tests/fixtures/golden_{corporate,individual}.{md,docx,xlsx,csv,pdf}` + 扫描版 `.scan.pdf`；生成器 `scripts/generate_golden_fixtures.py` 可复现 |
| 候选召回率 ≥ 90%、精确率 ≥ 95% | ✅ | `docs/release/golden-report.md`：全部字段 1.00/1.00（原生文本层）；`test_golden_extraction.py` 强制阈值 |
| 红线关键输入 100% 找到或明确缺失 | ✅ | 红线关键字段（贷款金额/期限/利率/还款方式/必要费用/罚息利率）全部出现在黄金集与演示流中；`test_redline.py` 关键输入缺失时绝不判“未触发” |
| 抽取指标报告 | ✅ | `docs/release/golden-report.md`（原生）与 `docs/release/golden-report-ocr.md`（固定 PaddleOCR 模型）；CI 做 `--check-drift` |
| 固定 PaddleOCR/模型黄金套件发布任务；依赖/模型变更必须重跑 | ✅ | `.github/workflows/release-golden.yml`：镜像内重跑 OCR 黄金集，与已提交报告不一致即失败 |

## 4. 规则与完备性

| 验收 | 状态 | 证据 |
| --- | --- | --- |
| 红线期望结果一致率 100% | ✅ | `tests/test_redline.py`（表驱动期望结果 + 不可变快照） |
| 演示完备性清单项/条件覆盖 100% | ✅ | `tests/test_completeness.py`：`test_conditions_and_items_cover_each_other`、`test_every_demo_item_reaches_every_reachable_state` |

## 5. 200 页参考工作量

| 验收 | 状态 | 证据 |
| --- | --- | --- |
| 200 页基准在参考硬件 P95 ≤ 10 分钟，命令/数据/结果已记录 | ✅ | `docs/release/benchmark-200p.md`（原生 P95≈4s）与 `benchmark-200p-ocr.md`（含印章检测全管线 P95≈8min，8 核机器）；脚本 `backend/scripts/benchmark_200p.py` 可重复 |

## 6. 备份与恢复

| 验收 | 状态 | 证据 |
| --- | --- | --- |
| 加密、成套（PostgreSQL + MinIO + 已发布配置）备份，哈希清单，隔离目标 | ✅ | `scripts/backup.sh`（AES-256-CBC + PBKDF2、`MANIFEST.sha256`、`--target` 隔离、`--verify`、`--keep`） |
| 恢复脚本与非生产演练 | ✅ | `scripts/restore.sh`；演练记录：`down -v` → 恢复 → 全栈启动 → 99 申请/7 用户/76 材料/22 红线/13 完备性记录完整 → 管理员登录与列表 API 200 → e2e 复跑通过 |
| RPO 24h / RTO 4h 基线 | ✅ | `docs/ops/backup-restore.md`（调度示例 + RTO 构成说明） |

## 7. 生产安全姿态

| 验收 | 状态 | 证据 |
| --- | --- | --- |
| 无生产默认凭据/规则/模板/LPR | ✅ | `docker-compose.prod.yml` 仅 secret 文件提供凭据；生产模式不注入演示数据并拒绝演示正式报告；`tests/test_production_compose.py` 断言 |
| 运行时无静默模型下载/更新 | ✅ | 镜像构建期下载并 SHA-256 校验；worker 启动 `verify_artifacts` 校验（`tests/test_paddle_engine.py`） |

## 8. 文档

| 交付 | 证据 |
| --- | --- |
| 部署 | `docs/ops/deployment.md` |
| 首个管理员与演示/生产初始化 | `docs/ops/initialization.md` |
| TLS/机密/卷加密职责 | `docs/ops/security.md` |
| 故障排查 | `docs/ops/troubleshooting.md` |
| 模型升级与黄金集重跑 | `docs/ops/model-upgrade.md` |
| 备份/恢复 | `docs/ops/backup-restore.md` |
| 发布检查 | `docs/ops/release-check.md` |

## 9. 试点就绪门禁（明确未完成，不得据此宣称生产可用）

以下项目属于试点就绪而非工程完成；本报告不声明它们已满足：

- [ ] **真实脱敏材料与黄金集**：机构授权的真实脱敏材料及其标签
- [ ] **机构法务/业务批准**：机构对规则、模板、LPR 适用性的批准
- [ ] **模型供应商协议**：“不用于训练”、留存期限、保密条款
- [ ] **基础设施加密与保留制度**：TLS 续期、数据卷加密、备份加密与材料/备份保留制度书面化并执行
- [ ] **生产级恢复演练**：在真实生产环境（非一次性演练栈）完成恢复并验收
- [ ] **≤ 15 分钟真实试点**：审批人员在真实试点中的主动人工操作时长验收

满足以上全部门禁之前，系统状态为“工程完成”，**不是**“生产可用”。
