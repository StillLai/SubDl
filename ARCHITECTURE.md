# Architecture

## 数据流

```
SUB_URL / GIST_URL (env)
  → parse_subscriptions()        # 从 providers.jsonc 读取 $ENV_VAR 映射，区分 SUB_URL/GIST_URL
  → _download_all()              # 并行下载订阅（ThreadPoolExecutor）
  → _try_gist_fallback()         # 失败时从 Gist 备份获取已转换的 sing-box JSON
  → _convert_batch()             # Node.js 批量转换（subprocess，单进程复用）
  → _aggregate_results()         # 聚合结果：收集文件、订阅信息、节点字典
  → _load_templates()            # 加载模块化模板（base/dns/route/outbounds + inbounds 变体）
  → merge_all_templates()        # 将节点合并到各变体模板
  → generate_provider_configs()  # 生成 providers 版本（use_gist 的 provider 指向 Gist）
  → _validate_configs()          # sing-box check 校验（official + reF1nd 双二进制）
  → upload_to_gist()             # 清理旧文件 + 上传新文件到 Gist
```

## 模块依赖关系

```
update_subscriptions.py (主入口)
  ├── utils.py         (基础工具，无项目内依赖)
  ├── merge_config.py  (模板合并，依赖 utils)
  ├── svg.py           (SVG 生成，依赖 utils)
  └── convert.mjs      (Node.js，通过 subprocess 调用)

ruleset_convert.py     (独立入口，由 Ruleset Update workflow 调用)
  └── utils.py
```

## 文件职责

| 文件 | 职责 | 关键函数 |
|------|------|----------|
| utils.py | 常量、数据模型、网络、日志、异常类 | `http_get_with_retry`, `load_jsonc`, `log_info/warn/error` |
| update_subscriptions.py | 主流程：下载 → 转换 → 合并 → 上传 | `main()`, `parse_subscriptions()`, `_download_all()` |
| merge_config.py | 模板合并逻辑、provider 展开 | `merge_config()`, `process_providers()`, `filter_nodes_by_regex()` |
| convert.mjs | Clash → sing-box 批量转换 | `batchConvertToSingbox()` (Node.js) |
| ruleset_convert.py | 规则集下载与转换 | `parse_list_file()`, `_group_by_mapped()` |
| svg.py | 状态图生成（浅色/深色主题） | `generate_status_svg()` |

## 关键设计决策

### 为什么用 Gist 而不是仓库存储？
- 订阅内容包含敏感 token（机场订阅链接），不适合放在仓库中
- Gist 支持通过 API 动态更新文件，适合每次 workflow 运行后刷新

### 为什么批量 Node.js 转换？
- Sub-Store 的 proxy-utils 模块加载开销大（需要 jsdom 环境、依赖注入等）
- 单进程复用比每次 fork 新进程快得多
- 所有 Clash 内容通过临时文件传入，JSON 结果通过 stdout 传出

### Provider 前缀机制
- merge_config 步骤 1 中会给所有节点 tag 加上 `"{sub_name}/"` 前缀
- process_providers 展开 provider 引用时使用 `"{provider_tag}/{node_tag}"` 格式
- 这确保了不同订阅的同名节点不会冲突

### 模板模块化设计
- `config_template/` 拆分为公共零件（base/dns/route/outbounds/providers）+ 变体（inbounds/）
- 公共零件只读一次，与每个变体组装成完整配置
- 新增平台变体只需在 `inbounds/` 目录下添加一个 JSONC 文件

### 模板共享对象的防御性复制
- `_load_templates()` 中多个模板共享同一个 `providers` 列表对象（仅读取一次）
- 任何需要修改模板数据的函数（如 `generate_provider_configs()`）必须先 `copy.deepcopy()`
- 不做深拷贝会导致第一个模板处理后的原地修改影响所有后续模板

### Provider 配置与直接合并的区别
- **直接合并**：所有节点直接写入 outbounds，配置文件较大但自包含
- **Provider 版本**：outbounds 中的 urltest 指向远程订阅 URL，客户端自行拉取节点
- Provider 版本中 `GIST_URL*` 环境变量的 provider URL 指向 Gist 上已转换的 sing-box 文件

### 为什么完全运行在 GitHub Actions CI 上？
- 项目的核心功能（下载订阅、调用 Node.js 转换、调用 sing-box 校验、上传 Gist）依赖 CI 环境中的 Secrets（GH_TOKEN、GIST_ID）和预装的二进制（sing-box）
- 本地无法完整运行主流程，因此不需要 Dry-run 模式或本地预览命令
- 代码正确性通过 `git push` → CI 运行来验证

### 为什么不做节点去重？
- 不同订阅来源不会出现相同节点（不同机场使用不同的服务器），去重逻辑无实际收益
- 即使极小概率出现重复，对功能也没有影响（selector 中多一个等价选项）

### 为什么不做地区分布统计？
- 模板中已通过正则 include/exclude 按地区分组节点（🇭🇰 香港、🇯🇵 日本等）
- 统计功能属于对模板分组逻辑的重复劳动，增加代码复杂度但收益极低

### 为什么不做订阅过期/流量耗尽预警？
- 机场服务商会在订阅到期前自行发送邮件提醒
- 程序额外实现预警功能属于重复劳动，且增加不必要的依赖（如通知服务配置）

### 为什么不做 HTTP 超时可配置化？
- 当前硬编码的 30 秒超时已覆盖绝大多数网络场景
- 增加环境变量（如 HTTP_TIMEOUT）让用户配置更繁琐，收益不大
- 如果真有特殊场景需要调整，可以直接修改代码中的常量

## 错误处理体系

```
SubDlError (基础异常)
  ├── ConfigError       # 环境变量或配置缺失/无效
  ├── DownloadError     # 订阅下载失败
  ├── ConversionError   # Clash → sing-box 转换失败
  ├── TemplateError     # 模板加载或合并失败
  ├── ValidationError   # sing-box check 配置校验失败
  └── UploadError       # Gist API 上传失败
```

所有异常携带 `context` 字典，提供结构化的错误上下文。

## 环境变量

### 必需
| 变量 | 说明 |
|------|------|
| `GH_TOKEN` | GitHub Personal Access Token（gist 权限） |
| `GIST_ID` | 目标 Gist ID |
| `SUB_URL` | 主订阅链接（纯 URL） |
| `SING_BOX_BIN` | sing-box 官方二进制路径（校验用） |

### 可选
| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SUB_URL_1` ~ `_9` | 更多常规订阅（纯 URL） | — |
| `GIST_URL` / `GIST_URL_1` ~ `_9` | Gist 订阅（纯 URL），provider 指向 Gist 已转换文件 | — |
| `SING_BOX_REF1ND_BIN` | sing-box reF1nd 二进制路径（providers 校验用） | 回退到官方版 |
| `WORKERS` | 并行下载线程数 | `8` |

### SUB_URL / GIST_URL 约定
- `providers.jsonc` 中 provider 的 `url` 字段为 `$ENV_VAR` 占位符（如 `$SUB_URL_1`、`$GIST_URL`）
- `SUB_URL` / `SUB_URL_N` — 常规订阅，provider URL 指向原始订阅地址
- `GIST_URL` / `GIST_URL_N` — Gist 订阅，provider URL 指向 Gist 上已转换的 sing-box 文件
- 环境变量值为纯 URL，订阅名称由 `providers.jsonc` 中 provider 的 `tag` 字段决定
