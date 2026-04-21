# SubDl

定期拉取 Clash 订阅并上传到 GitHub Gists 的自动化工具。

## 功能

- 🔄 定期自动拉取 Clash 订阅（每6小时）
- 🔒 使用指定的 User-Agent 请求订阅
- 📤 自动上传到 GitHub Gists
- 🔐 敏感信息通过 Repository Secrets 管理
- 📝 支持多个订阅源配置

## 快速开始

### 1. Fork 本仓库

点击右上角的 "Fork" 按钮，将本仓库复制到你的 GitHub 账号下。

### 2. 配置 Secrets

进入仓库的 **Settings → Secrets and variables → Actions**，添加以下 Secrets：

| Secret 名称 | 必需 | 说明 |
|------------|------|------|
| `GH_TOKEN` | ✅ | GitHub Personal Access Token，需要 `gist` 权限 |
| `SUB_URL` | ✅ | 订阅链接（见下方配置方法） |
| `GIST_ID` | ❌ | 现有 Gist ID（首次运行可不设置，会自动创建） |
| `USER_AGENT` | ❌ | 自定义 User-Agent（默认: `clash-verge/v2.4.4`） |

### 3. 获取 GitHub Token

1. 访问 [GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)](https://github.com/settings/tokens)
2. 点击 "Generate new token (classic)"
3. 勾选 `gist` 权限
4. 生成后复制 token，添加到仓库 Secrets 中作为 `GH_TOKEN`

### 4. 配置订阅链接

每个订阅链接单独设置一个 Secret，格式如下：

| Secret 名称 | 说明 |
|------------|------|
| `SUB_URL` | 第一个订阅链接 |
| `SUB_URL_1` | 第二个订阅链接（可选） |
| `SUB_URL_2` | 第三个订阅链接（可选） |
| ... | 更多订阅链接（最多支持到 `SUB_URL_5`） |

**配置方法：**
1. 在 Secrets 页面点击 **New repository secret**
2. Name 填 `SUB_URL`，Value 填你的订阅链接
3. 如需添加更多订阅，继续添加 `SUB_URL_1`、`SUB_URL_2` 等

**示例：**
- `SUB_URL` = `https://example.com/subscription1`
- `SUB_URL_1` = `https://example.com/subscription2`

### 5. 手动运行测试

进入仓库的 **Actions → Update Subscriptions**，点击 "Run workflow" 手动触发一次测试。

## 工作流说明

- **定时运行**: 每6小时自动运行（UTC 时间 00:00, 06:00, 12:00, 18:00）
- **手动触发**: 支持随时手动运行测试
- **首次运行**: 如果不指定 `GIST_ID`，会自动创建新的 Gist，运行后请查看日志获取 Gist ID 并添加到 Secrets

## 获取订阅内容

上传成功后，订阅内容会保存在 Gist 中，可以通过以下方式获取：

1. 访问你的 Gist 页面：`https://gist.github.com/{username}/{gist_id}`
2. 点击对应文件的 "Raw" 按钮获取原始链接
3. 将该链接配置到 Clash 客户端中

## 目录结构

```
SubDl/
├── .github/
│   └── workflows/
│       └── update-subscriptions.yml  # GitHub Actions 工作流
├── src/
│   └── update_subscriptions.py        # 主程序
├── README.md
└── .gitignore
```

## 注意事项

1. **订阅内容不会保存在仓库中**，只上传到 Gists
2. **不要**在代码中直接写入订阅链接、Token 等敏感信息
3. GitHub Actions 对免费账号有 [使用限制](https://docs.github.com/en/actions/learn-github-actions/usage-limits-billing-and-administration)
4. 如果订阅下载失败，会在 Gist 中创建 `.errors` 文件记录错误

## 参考

- [sub-store](https://github.com/sub-store-org/Sub-Store) - 参考了订阅下载逻辑
- [GitHub Gist API](https://docs.github.com/en/rest/gists)

## License

MIT