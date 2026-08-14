# 初始化与首个管理员

## 演示环境（本地 Compose）

```bash
cp .env.example .env
docker compose up --build
```

- 启动时自动：等 postgres/minio 就绪、创建 `materials` 桶、执行 Alembic 迁移、注入演示完备性模板与演示规则包/LPR（仅开发模式）。
- 首次管理员（交互式，口令至少 12 位）：

```bash
docker compose exec api icrm-create-admin administrator
```

- 管理员登录后在“账号管理”创建审批人员账号；审批人员创建申请、上传材料并走完辅助审查流程。

## 生产环境（docker-compose.prod.yml）

见 `docs/ops/deployment.md`。生产模式不注入演示模板/规则，拒绝用演示配置生成正式报告。

## 无交互/自动化（CI、脚本）

`icrm-create-admin` 支持 `--password-stdin`，从标准输入读取两行（口令、确认）：

```bash
printf '%s\n%s\n' '新口令至少12位' '新口令至少12位' \
  | docker compose exec -T api icrm-create-admin --password-stdin administrator
```

CI 端到端测试用 `backend/scripts/seed_e2e.py` 一次性创建管理员与审批人员（幂等）：

```bash
docker compose exec -T api env \
  ICRM_E2E_ADMIN_USERNAME=e2e-admin ICRM_E2E_ADMIN_PASSWORD='...' \
  ICRM_E2E_OFFICER_USERNAME=e2e-officer ICRM_E2E_OFFICER_PASSWORD='...' \
  python -m scripts.seed_e2e
```

> 凭据只存在于本次执行的环境中，不写入镜像、日志或持久状态。

## 检查清单

- [ ] 系统不存在默认账号或默认口令
- [ ] 首个管理员由操作者亲自设置且强度 ≥ 12 位
- [ ] 审批人员账号按最小权限开通（角色为 `approval_officer`）
- [ ] 演示环境可自由重置（`docker compose down -v`）；生产环境严禁在未备份时重置数据卷
