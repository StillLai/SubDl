# SubDl

> 最后更新: 2026-06-20 03:25:43 CST

## 订阅状态

| 订阅 | 总流量 | 已用 | 剩余 | 到期时间 | 状态 | 节点数 |
|------|--------|------|------|----------|------|--------|
| 山海 | 256.00 GB | 166.76 GB | 89.24 GB | 无 | ✅ 正常 | 30 |
| 飞鸟云 | 100.00 GB | 25.37 GB | 74.63 GB | 2026-11-14 | ✅ 正常 | 50 |
| **合计** | | | | | | **80** |

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
- `sing-box-config.json` 是可直接使用的完整sing-box配置文件
- 参考 [sub-store](https://github.com/sub-store-org/Sub-Store) 实现

## 🚀 sing-box 路由规则集

本项目自动将多种格式的规则源（Clash、Surge 等）转换为 sing-box 支持的规则集格式，支持：

- 🔄 **自动更新**：每 6 小时自动抓取最新规则并重新生成
- 📦 **双格式输出**：同时生成 JSON 格式（`ruleset/json/`）和 SRS 二进制格式（`ruleset/srs/`）
- ⚡ **性能优化**：SRS 格式加载更快，推荐使用
- 🛠️ **自定义规则**：修改 `ruleset/custom_rule/` 目录下的文件即可添加个人规则

### 自定义规则

| 文件 | 用途 |
|------|------|
| `custom_direct.list` | 直连域名/IP |
| `custom_proxy.list` | 代理域名/IP |
| `custom_block.list` | 屏蔽域名/IP |
| `custom_whitelist.list` | 白名单（强制直连） |
