# Changelog

本文件由开发者维护，记录每次重要的代码变更。
Vibe Coding 规范：每次代码变更完成后，应在此文件顶部追加一条记录。

---

## 2026-07-04

### Added
- 新增 `.clinerules` — AI Vibe Coding 项目规则文件
- 新增 `ARCHITECTURE.md` — 项目架构文档
- `_check_rate_limit()` — GitHub API 速率限制检测与预警（应用于 Gist 文件列表、上传、用户信息查询）

### Changed
- `_try_decode_base64()` 增加假阳性防护：短内容（< 100 字符）跳过检测，解码后验证可打印字符占比 ≥ 90%
- `_download_all()` 改用 `dict[int, DownloadResult]` 收集结果，消除 `[None] * n` 的 `type: ignore` 不安全写法
- 导入 `HttpResponse` 类型以支持速率限制检查函数的类型注解

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