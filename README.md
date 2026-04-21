# SubDl

> 最后更新: 2026-04-21 08:16:49 UTC

## 订阅状态

| 订阅 | 总流量 | 已用 | 剩余 | 到期时间 | 状态 |
|------|--------|------|------|----------|------|
| feiniaoyun | 100.00 GB | 48.17 GB | 51.83 GB | 2026-11-14 | ✅ 正常 |
| shanhai | 256.00 GB | 165.87 GB | 90.13 GB | 未知 | ✅ 正常 |

## 快速配置

1. Fork 本仓库
2. 在 Settings → Secrets → Actions 中添加:
   - `GH_TOKEN`: GitHub Token (需要 gist 权限)
   - `SUB_URL`: 订阅链接 (`名称|URL` 格式)
   - `SUB_URL_1`, `SUB_URL_2`...: 更多订阅（可选）
3. 在 Actions → Update Subscriptions 中点击 Run workflow

## 功能特性

- 每 6 小时自动更新订阅
- 订阅内容上传到 Gist，不保存在仓库
- **自动转换 Clash 订阅为 Sing-box 格式**（使用 [Sub-Store](https://github.com/sub-store-org/Sub-Store) 核心）

## 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `GH_TOKEN` | GitHub Token（需要 gist 权限） | 必填 |
| `GIST_ID` | 已有的 Gist ID（可选） | 空 |
| `SUB_URL` | 订阅链接（`名称\|URL` 格式） | 必填 |
| `SUB_URL_1`~`SUB_URL_9` | 更多订阅链接 | 可选 |
| `USER_AGENT` | 下载订阅时的 User-Agent | `clash-verge/v2.4.4` |
| `ENABLE_SINGBOX_CONVERT` | 是否启用 Sing-box 转换 | `true` |

## Sing-box 转换说明

- 下载订阅后，自动使用 Sub-Store 的核心库将 Clash 格式转换为 Sing-box JSON 格式
- 转换后的文件命名为 `{订阅名}-singbox.json`
- 原始 Clash YAML 和 Sing-box JSON 会一起上传到 Gist
- 依赖 `proxy-utils.esm.mjs` 会在运行时自动从 Sub-Store Releases 下载并自动更新

## 参考

- [sub-store](https://github.com/sub-store-org/Sub-Store)
