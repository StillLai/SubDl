# SubDl

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://github.com/StillLai/SubDl/raw/refs/heads/assets/status-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://github.com/StillLai/SubDl/raw/refs/heads/assets/status-light.svg">
  <img src="https://github.com/StillLai/SubDl/raw/refs/heads/assets/status-light.svg" alt="订阅状态">
</picture>

> 自动下载 Clash 订阅 → 转换为 sing-box 格式 → 合并模板配置 → 上传到 GitHub Gist

## 功能特性

- 🔄 每小时自动更新订阅（GitHub Actions cron）
- ⚡ 并行下载 + 自动重试（指数退避）
- 🔀 Clash → sing-box 自动转换（基于 [Sub-Store](https://github.com/sub-store-org/Sub-Store)）
- 📦 多模板配置生成（tun / mixed / tproxy / tun-win）
- 📊 流量状态 SVG 图片展示（上传到 `assets` 分支）
- 📋 自定义规则集支持（Clash/Surge → sing-box JSON + SRS）

## 快速配置

### 1. 创建 Gist

前往 [GitHub Gist](https://gist.github.com/) 创建一个新的 Gist（可以是空文件），记录下 Gist ID（URL 最后一段，如 `https://gist.github.com/user/abc123` 中的 `abc123`）。

### 2. Fork 本仓库

### 3. 配置 Secrets

在仓库的 **Settings → Secrets and variables → Actions** 中添加：

| Secret | 必填 | 说明 |
|--------|------|------|
| `GH_TOKEN` | ✅ | GitHub Personal Access Token，需要 `gist` 权限 |
| `GIST_ID` | ✅ | 上一步创建的 Gist ID |
| `SUB_URL` | ✅ | 主订阅链接（纯 URL） |
| `SUB_URL_1` ~ `SUB_URL_9` | ❌ | 更多常规订阅（可选，纯 URL） |

**订阅链接示例：**

环境变量值为纯 URL，订阅名称由 `providers.jsonc` 中对应 provider 的 `tag` 字段决定。

```
SUB_URL = https://example.com/api/sub?token=xxx
SUB_URL_1 = https://example2.com/clash/config
```

**Provider 指向 Gist：**

在 `providers.jsonc` 中使用 `$SUB_URL_GIST`（而非 `$SUB_URL`）作为占位符，可让该订阅在 providers 配置中的 provider `url` 指向 Gist 上已转换的 sing-box 文件（`{tag}-singbox.json`），而非原始订阅地址。实际的订阅 URL 从对应的 `$SUB_URL` 环境变量获取，无需额外配置。

```jsonc
{
  "url": "$SUB_URL_GIST_1",  // 标记：provider 指向 Gist（实际 URL 从 SUB_URL_1 获取）
  "tag": "雪山"
}
```

生成的 provider URL 格式为：`https://ghfast.top/https://gist.github.com/{用户名}/{Gist ID}/raw/{tag}-singbox.json`（通过 `ghfast.top` 加速访问）

这在 sing-box 客户端无法直接解析 Clash 订阅格式时很有用——provider 会直接拉取已在服务端转换好的 sing-box JSON。

### 4. 运行

在 **Actions → Subscriptions Update** 中点击 **Run workflow**，等待完成后即可在 Gist 中查看生成的配置文件。

## 输出文件

运行成功后，Gist 中会生成以下文件：

| 文件名 | 说明 |
|--------|------|
| `{订阅名}.yaml` | 原始订阅内容 |
| `{订阅名}-singbox.json` | 转换后的 sing-box 节点 |
| `sing-box-{变体名}.json` | 合并对应入站变体的完整配置 |
| `sing-box-{变体名}-providers.json` | 使用 providers 引用节点的配置 |

变体名取自 `config_template/inbounds/` 目录下的文件名，例如 `tun.jsonc` → `sing-box-tun.json`。

## 配置模板

`config_template/` 目录采用模块化结构，公共部分只需编辑一次：

| 零件文件 | 说明 |
|----------|------|
| `base.jsonc` | 基础配置（log / experimental / clash_api / cache_file） |
| `dns.jsonc` | DNS 服务器和路由规则 |
| `providers.jsonc` | 节点 providers 数组 |
| `outbounds.jsonc` | 出站规则（分组选择器、urltest 等） |
| `route.jsonc` | 路由规则（rule_set + rules） |
| `inbounds/` | 入站变体，每个文件对应一种配置 |

入站变体：每个文件自动生成对应的配置（`sing-box-{文件名}.json`），新增变体只需在 `inbounds/` 目录下添加文件即可。

## 自定义规则集

编辑 `ruleset/ruleset_source.txt` 添加规则源链接（每行一个 URL），支持：

- sing-box JSON 规则集（直接使用）
- Clash/Surge 规则文件（`.yaml` / `.txt` / `.csv`，自动转换）

规则集更新由独立的 **Ruleset Update** workflow 每 6 小时自动运行，生成的 JSON 和 SRS 文件会上传到 GitHub Releases。

## 项目结构

```
SubDl/
├── src/
│   ├── update_subscriptions.py   # 主流程：下载 → 转换 → 合并 → 上传
│   ├── convert.mjs               # Clash → sing-box 转换（Node.js）
│   ├── merge_config.py           # 将节点合并到模板配置
│   ├── ruleset_convert.py        # 规则集转换脚本
│   └── utils.py                  # 公共工具（网络、日志、数据模型）
├── config_template/              # sing-box 配置模板（模块化零件）
│   ├── base.jsonc                # 基础配置（log/experimental/clash_api/cache_file）
│   ├── dns.jsonc                 # DNS 配置
│   ├── providers.jsonc           # providers 数组
│   ├── outbounds.jsonc           # outbounds 数组
│   ├── route.jsonc               # 路由配置（rule_set + rules）
│   └── inbounds/                 # 入站变体（tun/mixed/tproxy/tun-win）
├── ruleset/                      # 规则集源和自定义规则
├── sing-box-reF1nd-docs/         # sing-box 文档副本
└── .github/workflows/            # CI 自动化
```

## 说明

- 订阅内容上传到 Gist，不保存在仓库
- `GH_TOKEN` 建议使用 Fine-grained Token，仅授予 Gist 读写权限
- `GIST_ID` 为必填项，必须在首次运行前手动创建并配置
- 参考 [Sub-Store](https://github.com/sub-store-org/Sub-Store) 的 `proxy-utils.esm.mjs` 实现 Clash → sing-box 转换

## License

[MIT](LICENSE)
