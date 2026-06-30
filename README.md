# SubDl

![订阅状态](https://github.com/StillLai/SubDl/raw/refs/heads/assets/status.svg)

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
| `SUB_URL` | ✅ | 主订阅链接，格式为 `名称\|URL` |
| `SUB_URL_1` ~ `SUB_URL_9` | ❌ | 更多订阅（可选，格式同上） |
| `GIST_OWNER` | ❌ | Gist 所有者用户名（可选，自动通过 API 获取） |

**订阅链接示例：**
```
SUB_URL = 我的机场|https://example.com/api/sub?token=xxx
SUB_URL_1 = 备用订阅|https://example2.com/clash/config
SUB_URL_2 = https://example3.com/sub   # 省略名称时自动从 URL 提取
```

**Provider 指向 Gist：**

在订阅名称前加 `*`，可让该订阅在 providers 配置中的 provider `url` 指向 Gist 上已转换的 sing-box 文件（`{名称}-singbox.json`），而非原始订阅地址。Gist 所有者用户名会自动通过 API 获取，也可通过可选的 `GIST_OWNER` 环境变量手动指定。

```
SUB_URL = *山海|https://example.com/api/sub?token=xxx
```

生成的 provider URL 格式为：`https://gh-proxy.org/https://gist.github.com/{用户名}/{Gist ID}/raw/{名称}-singbox.json`（通过 `gh-proxy.org` 加速访问）

这在 sing-box 客户端无法直接解析 Clash 订阅格式时很有用——provider 会直接拉取已在服务端转换好的 sing-box JSON。

### 4. 运行

在 **Actions → Subscriptions Update** 中点击 **Run workflow**，等待完成后即可在 Gist 中查看生成的配置文件。

## 输出文件

运行成功后，Gist 中会生成以下文件：

| 文件名 | 说明 |
|--------|------|
| `{订阅名}.yaml` | 原始订阅内容 |
| `{订阅名}-singbox.json` | 转换后的 sing-box 节点 |
| `sing-box.json` | 合并默认模板的完整配置 |
| `sing-box-mixed.json` | 无 tun 模式的配置 |
| `sing-box-tproxy.json` | tproxy 模式的配置 |
| `sing-box-tun-win.json` | Windows 专用 tun 配置 |
| `sing-box-*-providers.json` | 使用 providers 引用的配置 |

## 配置模板

`config_template/` 目录下有 4 个模板变体：

| 模板文件 | 适用场景 |
|----------|----------|
| `sing-box.jsonc` | 默认配置（带 tun 入站） |
| `sing-box-mixed.jsonc` | 不需要 tun 的场景（如纯代理转发） |
| `sing-box-tproxy.jsonc` | Linux tproxy 透明代理 |
| `sing-box-tun-win.jsonc` | Windows（移除了 `auto_redirect`） |

你可以编辑基础模板 `sing-box.jsonc`，其他变体会在 CI 中自动生成。

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
│   ├── template_update.py        # 模板变体生成
│   └── utils.py                  # 公共工具（网络、日志、数据模型）
├── config_template/              # sing-box 配置模板
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

[GPL-3.0](LICENSE)