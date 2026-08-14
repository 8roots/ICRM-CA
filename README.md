# ICRM-CA

智能信贷风控合规助手。当前纵切提供本地账号认证、审批人员账号管理，以及企业/个人申请的创建和本人申请列表。

## Docker Compose 本地启动

要求：Docker Engine 与 Docker Compose v2。

```bash
cp .env.example .env
docker compose up --build
```

Compose 会自动等待 PostgreSQL 和 MinIO 就绪、创建 `materials` bucket、执行 Alembic migration，再启动 API、worker 和前端。打开 <http://localhost:8080>。

首次启动后，另开终端交互式创建唯一的首个管理员（密码必须由操作者输入且至少 12 个字符）：

```bash
docker compose exec api icrm-create-admin administrator
```

系统没有默认应用账号或密码。管理员登录后可在“账号管理”创建、启用或停用审批人员账号；审批人员可创建企业或个人申请，并且只能看到自己负责的申请。管理员在“申请元数据”页仅查看业务元数据并可重新分配负责人（原负责人立即失去访问权限，管理员自身不获得材料访问权限）。

材料解析完成后，worker 会先运行本地确定性规则抽取字段候选（企业/个人主体、拟议贷款、财务报表行、银行流水行、征信与抵押担保等），并为缺失目标字段定位最小切片、以单申请稳定别名脱敏后调用 OpenAI 兼容的 DeepSeek。字段候选不可修改或删除；审批人员在“字段候选复核与人工确认”页按置信度复核候选，并可跳转到原页证据，生成“采用 / 修正 / 人工录入”三类确认记录，人工录入值必须填写理由并标记无材料来源。

DeepSeek 默认关闭（仅在 `.env` 设置 `ICRM_DEEPSEEK_BASE_URL`、`ICRM_DEEPSEEK_API_KEY`、`ICRM_DEEPSEEK_MODEL` 后启用）。关闭或云端不可用时本地候选照常可用；脱敏失败会阻止云请求，只记录脱敏后的请求/响应审计（仅负责人可见）。

云端启用门禁：除密钥外，还必须设置 `ICRM_CLOUD_TRAINING_CONFIRMATION=true` 与明确的 `ICRM_CLOUD_RETENTION_DAYS`（供应商“不用于训练”与留存期限确认），否则 DeepSeek 保持关闭、本地处理继续；门禁状态在“字段候选复核”页与 `/health/ready` 可见。

## 生命周期、审计与删除

申请生命周期：草稿 → 处理中 → 待复核 → 辅助审查完成 → 已归档。

- “辅助审查完成”要求无运行中的处理任务，且最新红线与完备性正式报告存在且未失效；缺件、风险提示或资料不足仍可完成，但保持显式展示；
- 重新打开（从“辅助审查完成”或“已归档”）必须填写理由并记入审计；
- 已归档/已完成申请为只读：上传、重试、字段确认、清单映射、豁免与正式执行均被 API 拒绝；
- 管理员可重新分配负责人（元数据操作，不获得材料访问）；
- 管理员整笔硬删除采用两步：先填理由获取短期确认令牌，再凭令牌二次确认执行；硬删除级联清除 MinIO 原件、解析结果、候选、确认值与报告，仅保留不含业务敏感内容的审计墓碑（申请内部 ID、操作者、时间、删除理由）；删除失败的 MinIO 对象会记录在墓碑上以便恢复。

审计：登录/登出、上传/查看/下载、分配、字段确认、模板/规则/LPR 发布、正式评估、云调用元数据、归档/重开/删除均产生不可修改、不可删除的审计事件（操作者、时间、关联 ID、非敏感元数据）。管理员在“审计日志”页查询；审批人员仅可见自己申请的审计事件。云调用的脱敏请求/响应仍保存在受限的云调用记录中，仅负责人可见。

管理员“任务队列”页展示各状态任务数、等待最久的任务、最近失败与重试原因，以及 worker 心跳；`/health/ready` 在数据库、对象存储或（有待处理任务时的）worker 心跳异常时返回 503 并逐项报告组件状态。日志为 JSON 结构化输出，带关联 ID 或任务 ID，不记录材料正文、提示词正文或模型响应正文。

停止服务：

```bash
docker compose down
```

如需同时清空本地数据库和对象存储：

```bash
docker compose down -v
```

> 本地 Compose 通过 HTTP 提供服务，因此显式设置 `ICRM_COOKIE_SECURE=false`。生产部署见下方“生产部署”。

## 生产部署

生产 Compose（`docker-compose.prod.yml`）只暴露 TLS 反向代理（`proxy`），Postgres、MinIO、API 与 worker 均不发布端口；生产模式（`ICRM_PRODUCTION=true`、`ICRM_COOKIE_SECURE=true`）不加载演示模板/规则，并拒绝用演示配置生成正式报告。

```bash
cp -a .env.prod.example ./secrets   # 逐文件填写真实值
mkdir -p certs && cp <证书链> certs/fullchain.pem && cp <私钥> certs/privkey.pem
docker compose -f docker-compose.prod.yml up --build -d
```

凭据全部来自挂载的 secret 文件（`./secrets/*`，容器内 `/run/secrets/*`），配置支持 `*_FILE` 环境变量读取（如 `ICRM_DATABASE_URL_FILE`、`ICRM_DEEPSEEK_API_KEY_FILE`、`ICRM_MINIO_ACCESS_KEY_FILE`、`ICRM_MINIO_SECRET_KEY_FILE`），不会出现在环境变量或日志中。

## 后端开发检查

要求：Python 3.11、[`uv`](https://docs.astral.sh/uv/)。

```bash
cd backend
uv sync --frozen
uv run ruff format --check app tests alembic scripts
uv run ruff check app tests alembic scripts
uv run pytest
uv run python scripts/export_openapi.py
uv run python scripts/evaluate_extraction.py --check-drift
```

- `scripts/evaluate_extraction.py` 对黄金集材料（md/docx/xlsx/csv/pdf + 扫描版）运行抽取并报告逐字段召回率/准确率（阈值：召回率 ≥ 0.9、准确率 ≥ 0.95），未达标时以非零码退出；`--check-drift` 校验已提交的 `docs/release/golden-report.md` 未过期；`--engine ocr` 使用固定 PaddleOCR 模型（`ICRM_MODELS_DIR`）。
- `scripts/generate_golden_fixtures.py` 从 `tests/fixtures/golden_*.md` 复现全部格式语料。
- `scripts/benchmark_200p.py` 生成 200 页参考 PDF 并测量 P95，结果写入 `docs/release/benchmark-200p.md`。
- `scripts/seed_e2e.py` 为 CI/端到端从全新克隆创建管理员与审批人员（凭据来自环境变量）。
- CI 账号创建也可用 `icrm-create-admin --password-stdin`（无交互）。

数据库迁移：

```bash
cd backend
ICRM_DATABASE_URL='postgresql+psycopg://...' uv run alembic upgrade head
```

存活与就绪端点分别为 `/health/live` 和 `/health/ready`；就绪检查逐项报告数据库、对象存储、worker 心跳与云端门禁状态，异常时返回 503。OpenAPI 文档为 `/docs`，所有业务 API 位于 `/api/v1`。

## 前端开发检查

要求：Node.js 22、npm 10。先生成后端 OpenAPI 文件，再生成 TypeScript 类型：

```bash
cd backend && uv run python scripts/export_openapi.py
cd ../frontend
npm ci
npm run generate:api
npm run typecheck
npm test
npm run build
npm audit --audit-level=high
```

提交前可用下列命令确认 OpenAPI 产物没有漂移：

```bash
git diff --exit-code -- backend/openapi.json frontend/src/api/generated.ts
```

## CI 与发布

- `.github/workflows/ci.yml`：后端格式化/静态/单元/集成测试、前端类型/Vitest/构建、OpenAPI 与黄金报告漂移、`pip-audit`/`npm audit` 漏洞扫描、Compose 冒烟、Playwright 端到端（含企业/个人演示流）。CI 账号由脚本按运行生成，无隐藏状态。
- `.github/workflows/release-golden.yml`：固定 PaddleOCR 模型黄金套件发布任务；模型/依赖/规则变更必须同步重跑 `docs/release/golden-report-ocr.md`，否则任务失败。
- 发布前检查清单：`docs/ops/release-check.md`。
- 工程完成报告与试点就绪门禁：`docs/release/engineering-complete.md`。

## 运维文档

`docs/ops/`：部署（`deployment.md`）、初始化与首个管理员（`initialization.md`）、安全职责（`security.md`）、故障排查（`troubleshooting.md`）、模型升级（`model-upgrade.md`）、备份恢复（`backup-restore.md`）。

## 备份与恢复（RPO 24h / RTO 4h）

```bash
# 加密备份（PostgreSQL + MinIO + 已发布配置，SHA-256 清单）
scripts/backup.sh --compose docker-compose.prod.yml \
  --target /mnt/backup-disk/icrm --passphrase-file /root/.backup-pass --verify

# 恢复（非生产演练或明确的灾难恢复需 --force）
scripts/restore.sh --backup /mnt/backup-disk/icrm/<时间戳>-icrm-backup \
  --compose docker-compose.prod.yml --passphrase-file /root/.backup-pass --force
```

恢复演练流程与验收标准见 `docs/ops/backup-restore.md`。
