# 备份与恢复（RPO 24h / RTO 4h）

基线（工程完成阶段承诺）：**RPO 24 小时、RTO 4 小时**。备份由 `scripts/backup.sh` 生成，含 PostgreSQL 逻辑备份、MinIO `materials` 桶完整镜像、已发布配置（Compose 文件与环境模板）三部分，逐文件 AES-256-CBC + PBKDF2 加密，并以 SHA-256 清单收尾保证完整性。

## 备份

```bash
# 生产（加密）
scripts/backup.sh \
  --compose docker-compose.prod.yml \
  --target /mnt/backup-disk/icrm \
  --passphrase-file /root/.backup-pass \
  --verify

# 演示环境演练（明文，仅限非生产）
scripts/backup.sh --compose docker-compose.yml --no-encrypt
```

- `--target` 支持**隔离目标**：独立磁盘/挂载点（如 `/mnt/backup-disk/icrm`），避免与数据卷同盘。
- 每次备份生成时间戳目录 `<target>/<UTC时间>-icrm-backup/`：`postgres.sql.enc`、`materials/**`（加密镜像）、`config/*`、`backup.json`（组件元数据）、`MANIFEST.sha256`。
- `--verify` 在写入后立即校验清单；`--keep N` 可清理目标目录内更早的备份。
- 口令文件与 secrets 分开保管；口令丢失 = 备份不可恢复。

### 建议调度（RPO 24h）

```cron
# 每天 02:00 执行（示例 crontab）
0 2 * * * /opt/icrm/scripts/backup.sh --compose docker-compose.prod.yml \
  --target /mnt/backup-disk/icrm --passphrase-file /root/.backup-pass --verify --keep 14 >> /var/log/icrm-backup.log 2>&1
```

## 恢复

```bash
# 非生产演练 / 生产灾难恢复（明确知晓后加 --force）
scripts/restore.sh \
  --backup /mnt/backup-disk/icrm/<时间戳>-icrm-backup \
  --compose docker-compose.prod.yml \
  --passphrase-file /root/.backup-pass \
  --force
```

`restore.sh` 先校验 SHA-256 清单（失败即拒绝），再解密到暂存目录，然后恢复 PostgreSQL 与 MinIO，配置恢复到暂存目录由操作者手工应用（不覆盖活动 secrets）。

## 非生产恢复演练（rehearsal）

目标：证明备份可用且恢复后应用可运行、历史证据/报告完整。建议每季度一次，使用与生产一致的脚本但针对一次性 Compose 栈：

```bash
# 1. 用备份时的 compose 文件起一个空栈（不加载旧数据卷）
docker compose -f docker-compose.prod.yml down -v            # 演练专用环境
docker compose -f docker-compose.prod.yml up -d postgres minio

# 2. 恢复（演练环境允许 --force）
scripts/restore.sh --backup <备份目录> --compose docker-compose.prod.yml \
  --passphrase-file <口令文件> --force

# 3. 启动完整栈并验证
docker compose -f docker-compose.prod.yml up -d
curl -s https://演练主机/health/ready            # 期望 ready
# 登录管理员 → 抽查：申请列表/证据/历史正式报告仍存在（审计与报告快照不可变）
```

### 演练验收标准

- [ ] `MANIFEST.sha256` 校验通过
- [ ] PostgreSQL 行数/抽样内容与备份时刻一致
- [ ] MinIO 对象数/抽样原件可下载
- [ ] 应用可登录，历史红线/完备性报告与审计记录完好
- [ ] 记录演练日期、耗时、恢复到的目标与验收结果（写入本文件附录或运维日志）

> 生产环境的真实恢复演练（含 TLS、证书、机构数据）属于**试点就绪门禁**，工程完成阶段仅提供脚本与上述非生产演练流程。

## RTO 说明

RTO 4 小时包含：发现故障 → 拉起新主机/卷 → 恢复 PG + MinIO + 配置 → 启动应用 → 人工验收。脚本只覆盖“恢复数据并启动应用”部分；基础设施层面的磁盘/主机供给由部署方负责。
