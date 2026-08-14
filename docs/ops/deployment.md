# 部署（生产 Compose）

生产拓扑只有一个对外入口：TLS 反向代理（`proxy`）。PostgreSQL、MinIO、API 与 worker 全部位于 Compose 内部网络，不发布端口。所有凭据以挂载的 secret 文件形式提供（`./secrets/*`），生产模式不加载演示模板/规则。

## 前置条件

- Docker Engine 与 Docker Compose v2（生产机建议 Docker 24+ / Compose 2.20+）
- TLS 证书链与私钥（机构签发的合法证书；自签名仅可用于演示环境）
- 至少 4 核 / 8 GB 内存用于应用；PaddleOCR 模型约 26 MB（镜像构建时下载并校验）
- 磁盘：PostgreSQL 与 MinIO 数据卷的持久化存储；参考 8 核 / 32 GB 规格（见 `docs/release/benchmark-200p.md`）

## 步骤

```bash
# 1. 准备 secrets（逐文件填写真实值，不要保留示例口令）
cp -a .env.prod.example ./secrets
vim ./secrets/postgres_password
vim ./secrets/minio_root_user
vim ./secrets/minio_root_password
vim ./secrets/database_url        # postgresql+psycopg://icrm:<口令>@postgres:5432/icrm
: > ./secrets/deepseek_api_key    # 留空 = 关闭云端抽取

# 2. 准备 TLS 证书（只读挂载）
mkdir -p certs
cp <证书链> certs/fullchain.pem
cp <私钥>   certs/privkey.pem
chmod 600 certs/privkey.pem

# 3. 启动
docker compose -f docker-compose.prod.yml up --build -d
```

启动顺序由 Compose 保证：postgres/minio 就绪 → 建桶（`minio-init`）→ 迁移（`migrate`）→ api/worker → proxy 健康后对外服务。

## 首次管理员

```bash
docker compose -f docker-compose.prod.yml exec -T api \
  sh -c 'printf "%s\n%s\n" "$ICRM_ADMIN_PASSWORD" "$ICRM_ADMIN_PASSWORD" | icrm-create-admin --password-stdin administrator'
```

> 生产推荐交互式执行（`docker compose exec api icrm-create-admin administrator`），让管理员亲自输入口令；系统无默认账号。自动化环境可用 `--password-stdin` 两行输入口令与确认（见 `docs/ops/initialization.md`）。

## 验证

```bash
curl -s https://<主机名>/health/ready
# 期望 {"status":"ready", ...}
```

- `/health/live`：进程存活。
- `/health/ready`：数据库、对象存储、worker 心跳与云端门禁逐项报告，异常返回 503。
- 打开 https://<主机名>/ 用管理员登录，创建审批人员账号后即可使用。

## 更新与回滚

镜像标签为仓库提交；升级 = 更新代码后重新 `up --build`。回滚 = 回到上一提交重新构建。任何依赖或模型版本变更都必须重跑黄金集（见 `docs/ops/model-upgrade.md` 与 `docs/ops/release-check.md`）。
