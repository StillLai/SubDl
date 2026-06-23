# SubDl

> 最后更新: 2026-06-23 10:20:27 CST

## 订阅状态

| 订阅 | 总流量 | 已用 | 剩余 | 到期时间 | 状态 | 节点数 |
|------|--------|------|------|----------|------|--------|
| 山海 | 256.00 GB | 166.92 GB | 89.08 GB | 无 | ✅ 正常 | 30 |
| 飞鸟云 | 100.00 GB | 28.25 GB | 71.75 GB | 2026-11-14 | ✅ 正常 | 38 |
| **合计** | | | | | | **68** |

## 快速配置

1. Fork 本仓库
2. 在 Settings → Secrets → Actions 中添加:
   - `GH_TOKEN`: GitHub Token (需要 gist 权限)
   - `GIST_ID`: Gist ID（可选，首次运行后会自动创建并输出）
   - `SUB_URL`: 订阅链接 (`名称|URL` 格式)
   - `SUB_URL_1`, `SUB_URL_2`...: 更多订阅（可选）
3. 在 Actions → Subscriptions Update 中点击 Run workflow

## 说明

- 每小时自动更新订阅
- 订阅内容上传到 Gist，不保存在仓库
- `sing-box-config.json` 是可直接使用的完整 sing-box 配置文件
- 参考 [sub-store](https://github.com/sub-store-org/Sub-Store) 实现
