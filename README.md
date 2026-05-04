# SubDl

> 最后更新: 2026-05-04 08:08:07 CST

## 订阅状态

| 订阅 | 总流量 | 已用 | 剩余 | 到期时间 | 状态 | 节点数 |
|------|--------|------|------|----------|------|--------|
| feiniaoyun | 100.00 GB | 2.28 GB | 97.72 GB | 2026-11-14 | ✅ 正常 | 58 |
| shanhai | 256.00 GB | 165.92 GB | 90.08 GB | 无 | ✅ 正常 | 32 |
| **合计** | | | | | | **90** |

## 快速配置

1. Fork 本仓库
2. 在 Settings → Secrets → Actions 中添加:
   - `GH_TOKEN`: GitHub Token (需要 gist 权限)
   - `GIST_ID`: Gist ID（可选，首次运行后会自动创建并输出）
   - `SUB_URL`: 订阅链接 (`名称|URL` 格式)
   - `SUB_URL_1`, `SUB_URL_2`...: 更多订阅（可选）
3. 在 Actions → Update Subscriptions 中点击 Run workflow

## 说明

- 每小时自动更新订阅
- 订阅内容上传到 Gist，不保存在仓库
- `sing-box-config.json` 是可直接使用的完整sing-box配置文件
- 参考 [sub-store](https://github.com/sub-store-org/Sub-Store) 实现
