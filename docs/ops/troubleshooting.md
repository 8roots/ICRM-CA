# 故障排查

日志为 JSON 结构化输出（`docker compose logs -f api` / `worker`），带关联 ID 或任务 ID，不记录材料正文、提示词正文或模型响应正文。

## 健康检查失败

| 现象 | 排查 |
| --- | --- |
| `/health/ready` 返回 503 且 `database: error` | postgres 容器是否健康；`docker compose logs postgres`；磁盘是否满；迁移是否完成 |
| `object_store: error` | minio 容器是否健康；`materials` 桶是否存在（`minio-init` 是否成功）；MinIO 凭据是否与 secrets 一致 |
| `worker: error` 且有待处理任务 | worker 是否在运行；心跳文件是否新鲜（`ls -l /tmp/icrm-worker-heartbeat`）；查看 `worker` 日志 |
| `cloud_gate: blocked` | 属预期（未配置云端门禁）；配置见 README“云端启用门禁” |

## 上传/解析问题

- **“不支持旧版 Office / 含宏 / 压缩包”**：按提示转换格式（DOCX/XLSX/PDF/图片/Markdown/CSV）。
- **“材料已加密”**：解除 PDF 密码后重新上传。
- **“处理失败，签名与格式不匹配”**：文件损坏或 MIME 与实际格式不符；在文档详情页用“重跑解析”并填写原因。
- **worker 启动报模型缺失**：`pinned OCR model artifacts are absent or corrupted` → 镜像未正确构建或 `ICRM_MODELS_DIR` 指向错误目录；重建镜像（构建期会下载并校验模型）。

## 云端抽取（DeepSeek）未启用

- 检查 `ICRM_DEEPSEEK_BASE_URL`、`ICRM_DEEPSEEK_API_KEY`、`ICRM_DEEPSEEK_MODEL` 是否设置。
- 门禁：`ICRM_CLOUD_TRAINING_CONFIRMATION=true` 且 `ICRM_CLOUD_RETENTION_DAYS` 为明确数字，否则保持关闭（本地候选照常可用）。
- 云调用失败会回退本地候选；`redaction_failed` 会阻止云请求并仅记录脱敏审计。

## 页面/数据问题

- **申请只读**：已完成/已归档申请为只读；需“重新打开”并填写理由（记入审计）。
- **“尚不能标记完成”**：存在运行中任务，或最新红线/完备性正式报告缺失或失效；先完成正式评估。
- **状态显示陈旧**：详情页状态在完成/重开/归档后应立即刷新；若仍陈旧请硬刷新（Ctrl+Shift+R）并确认版本。

## 性能

- 参考基准：200 页文本 PDF 原生路径 P95 ≈ 4 s、含印章检测全管线 P95 ≈ 8 min（8 核机器，见 `docs/release/benchmark-200p.md`）。实际耗时随 CPU/磁盘而异。
- worker 每个任务会加载 PaddleOCR 模型；并发量大时按需调整 worker 副本或 `cpu_threads`。

## 备份/恢复

- 备份脚本报 `pg_dump failed`：确认栈在运行（`docker compose ps`）。
- `sha256sum -c` 失败：备份已损坏或部分写入，**不要**用该备份恢复；排查目标磁盘。
- 恢复报“hash manifest verification failed”：备份被篡改或损坏，拒绝恢复是预期行为。
