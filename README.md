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

系统没有默认应用账号或密码。管理员登录后可在“账号管理”创建、启用或停用审批人员账号；审批人员可创建企业或个人申请，并且只能看到自己负责的申请。

停止服务：

```bash
docker compose down
```

如需同时清空本地数据库和对象存储：

```bash
docker compose down -v
```

> 本地 Compose 通过 HTTP 提供服务，因此显式设置 `ICRM_COOKIE_SECURE=false`。生产部署必须在唯一反向代理入口配置 TLS、设置 `ICRM_COOKIE_SECURE=true`，并通过 secret 文件或 Docker secrets 提供 PostgreSQL/MinIO 凭据；不要使用 `.env.example` 的开发值。

## 后端开发检查

要求：Python 3.11、[`uv`](https://docs.astral.sh/uv/)。

```bash
cd backend
uv sync --frozen
uv run ruff check app tests alembic scripts
uv run pytest
uv run python scripts/export_openapi.py
```

数据库迁移：

```bash
cd backend
ICRM_DATABASE_URL='postgresql+psycopg://...' uv run alembic upgrade head
```

存活与就绪端点分别为 `/health/live` 和 `/health/ready`；OpenAPI 文档为 `/docs`，所有业务 API 位于 `/api/v1`。

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
