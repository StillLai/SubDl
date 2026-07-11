# Changelog

本文件由开发者维护，记录每次重要的代码变更。
Vibe Coding 规范：每次代码变更完成后，应在此文件顶部追加一条记录。
**同步检查：变更是否需要更新 `.clinerules` 或 `ARCHITECTURE.md`？（见 `.clinerules` "代码变更规范"）**

---

## 2026-07-11

### Fixed
- **Gist fallback URL 编码**：`_fetch_from_gist_fallback()` 和 `generate_provider_configs()` 中包含中文订阅名的 Gist URL 使用 `urllib.parse.quote` 编码，修复 `'ascii' codec can't encode characters` 错误导致 fallback 失败的问题
- **旧备份文件误删保护**：`_cleanup_old_gist_files()` 新增 `protected_filenames` 参数，保护所有仍在 `providers.jsonc` 中配置的订阅对应的文件（`.yaml` + `-singbox.json`），即使本次下载/转换失败也不会删除 Gist 上的备份

## 2026-07-08

### Changed
- **空出站处理逻辑重构**：当带有 include/exclude 的出站实际没有获得节点时，不再添加 Compatible fallback，而是直接删除该出站及所有出站中对它的引用（递归清理）
- `merge_config()` 返回值从 `dict` 改为 `tuple[dict, set[str]]`，额外返回被移除的空出站 tag 集合
- `merge_all_templates()` 返回值从 `dict[str, str]` 改为 `tuple[dict[str, str], set[str]]`，透传被移除的空出站 tag
- `generate_provider_configs()` 新增 `removed_tags` 参数，providers 版本复用合并阶段的空出站判断结果
- `_fix_empty_outbounds()` → `_remove_outbounds_by_tags()`：从添加 Compatible 转为删除空出站 + 清理引用

## 2026-07-07

### Fixed
- `README.md`：修正订阅链接配置说明——环境变量值为纯 URL（非 `名称|URL` 格式），订阅名称由 `providers.jsonc` 中 `tag` 字段决定；Gist 模式通过 `GIST_URL` 环境变量前缀区分（非名称前加 `*`）；补充 `GIST_URL` / `GIST_URL_N` 环境变量说明

### Changed
- `.clinerules`：代码变更规范新增第 9 条——涉及 README.md 内容的变更必须同步更新 README.md
- GitHub 代理前缀统一从 `gh-proxy.org` 更换为 `ghfast.top`（涉及 `update_subscriptions.py` 的 `generate_provider_configs()` 和 `README.md` 文档）

## 2026-07-05

### Fixed
- `docs-deploy.yml`：修复文档中绝对路径链接在 GitHub Pages 子路径 `/SubDl/` 下 404 的问题（如 `[拨号字段覆写](/zh/configuration/provider/override_dialer/)` 解析为 `https://stilllai.github.io/zh/...` 而非 `https://stilllai.github.io/SubDl/zh/...`），构建前通过 sed 将所有 `](/x` 统一替换为 `](/SubDl/x`

### Changed
- `ruleset-update.yml`：改为每次运行时删除旧 release 并重新创建，使 tag 发布时间保持最新（GitHub API 不支持通过 PATCH 修改 `published_at`）
- `ruleset-update.yml`：丰富 release notes 内容，动态生成构建时间、sing-box 版本、规则集文件列表及中文使用说明
  - 修复：sing-box 版本提取改用正则匹配，解决 `awk '{print $2}'` 提取到 "version" 字面值的问题
  - 规则集列表改为 Markdown 折叠列表 + 每行一项，提升可读性
- `.clinerules`：代码变更规范新增第 9 条——`attempt_completion` 前必须逐条验证所有文档更新已实际执行

## 2026-07-04

### Added
- 新增 `.clinerules` — AI Vibe Coding 项目规则文件
- 新增 `ARCHITECTURE.md` — 项目架构文档
- `_check_rate_limit()` — GitHub API 速率限制检测与预警（应用于 Gist 文件列表、上传、用户信息查询）

### Fixed
- `generate_provider_configs()` 共享 providers 列表导致只有第一个模板生成 providers 配置，使用 `copy.deepcopy()` 隔离每个模板

### Changed
- **订阅地址获取方式重构**：`providers.jsonc` 中 `url` 字段改为 `$ENV_VAR` 占位符，环境变量值为纯 URL，订阅名称由 `tag` 字段决定；`SUB_URL`/`GIST_URL` 前缀区分常规订阅与 Gist 订阅
- `parse_subscriptions()` 从 providers.jsonc 读取 `$ENV_VAR` 映射，不再解析 `名称|URL` 格式
- `generate_provider_configs()` 签名简化，直接从 `sub_by_env` 映射中获取订阅信息
- `_generate_and_upload()` 移除 `sub_url_map`/`gist_subs` 参数
- `_try_decode_base64()` 增加假阳性防护：短内容（< 100 字符）跳过检测，解码后验证可打印字符占比 ≥ 90%
- `_download_all()` 改用 `dict[int, DownloadResult]` 收集结果，消除 `[None] * n` 的 `type: ignore` 不安全写法
- 导入 `HttpResponse` 类型以支持速率限制检查函数的类型注解
- `.clinerules` 代码变更规范新增第 8 条：涉及代码或架构变更时需更新 `CHANGELOG.md`（纯模板文件变更除外）

## 2026-07-03

### Fixed
- `utils.py` / `ruleset_convert.py` 改进错误处理和健壮性

### Changed
- `route.jsonc` 路由配置重写，提升可维护性
- 模板文件添加 `$schema` JSON Schema 校验
- SVG 版本号添加超链接（指向 GitHub Releases）

### Removed
- GPLv3 许可证替换为 MIT

## 2026-07-02

### Changed
- SVG 生成重构：拆分子函数、优化模板加载
- 路由规则重新排序，SVG 布局缩放适配

### Added
- SVG 新增深色模式支持（浅色/深色双主题）
- SVG 状态图添加配置校验警告信息

### Fixed
- `053e5c2` 实现自定义异常体系（SubDlError 及子类）

## 2026-07-01

### Changed
- Workflow 引入可复用的 sing-box setup action
- 模板配置模块化重构（base/dns/route/outbounds/providers + inbounds/ 变体）
- 使用动态命名生成 sing-box 配置文件名

### Added
- Gist 上传时自动清理旧文件（`_cleanup_old_gist_files`）
- TUN 入站配置更新 `auto_redirect`

## 2026-06-30

### Fixed
- Gist 上传正确处理 `None` content（删除文件）

### Changed
- SVG 主题配置引入 `SvgTheme` dataclass 对象

## 2026-06-29

### Fixed
- 订阅下载函数添加 `env_name` 参数，SVG 中显示环境变量名

### Changed
- 多次模板变体更新

## 2026-06-28

### Added
- 规则集更新 workflow 添加验证和清理步骤

### Changed
- 移除 `log_debug`，提取 SVG 子函数，优化模板加载
- 启用 `from __future__ import annotations`
- 移除废弃代码，改进 HTTP 重试逻辑

---

> 格式参考：[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)
> 类别：`Added` / `Changed` / `Fixed` / `Removed` / `Deprecated` / `Security`