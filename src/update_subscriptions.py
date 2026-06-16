#!/usr/bin/env python3
"""
Subscription Downloader and Gist Uploader
"""

from __future__ import annotations

import os
import sys
import base64
import re
import time
import json
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, TypedDict
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

import requests

from utils import load_jsonc, discover_template_files, log


class Subscription(TypedDict):
    name: str
    url: str
    filename: str


class FlowInfo(TypedDict, total=False):
    upload: int
    download: int
    total: int
    expire: int | None


class SubscriptionInfo(TypedDict, total=False):
    name: str
    flow: FlowInfo | None
    node_count: int
    status: str


class SubscriptionResult(TypedDict, total=False):
    name: str
    status: str
    flow: FlowInfo | None
    reason: str
    filename: str | None
    raw_content: str
    node_count: int
    singbox_nodes: list[dict[str, Any]]
    singbox_content: str

# ========== 路径常量 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, 'config_template')
WORKFLOW_PATH = os.path.join(PROJECT_ROOT, '.github', 'workflows', 'subscriptions-update.yml')
CONVERT_SCRIPT = os.path.join(SCRIPT_DIR, 'convert.mjs')
MERGE_SCRIPT = os.path.join(SCRIPT_DIR, 'merge_config.py')
TEMPLATE_BASE = os.path.join(TEMPLATE_DIR, 'sing-box_template.jsonc')


def get_env_var(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise ValueError(f"环境变量 {name} 未设置")
    return value


def parse_flow_info(headers: dict[str, str]) -> dict[str, int] | None:
    """从响应头解析流量信息"""
    flow_header = headers.get('subscription-userinfo', '')
    if not flow_header:
        return None

    upload = re.search(r'upload=(\d+)', flow_header)
    download = re.search(r'download=(\d+)', flow_header)
    total = re.search(r'total=(\d+)', flow_header)
    expire = re.search(r'expire=(\d+)', flow_header)

    return {
        'upload': int(upload.group(1)) if upload else 0,
        'download': int(download.group(1)) if download else 0,
        'total': int(total.group(1)) if total else 0,
        'expire': int(expire.group(1)) if expire else None,
    }


def format_bytes(bytes_val: int) -> str:
    """格式化字节数"""
    if bytes_val == 0:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_idx = 0
    while bytes_val >= 1024 and unit_idx < len(units) - 1:
        bytes_val /= 1024
        unit_idx += 1
    return f"{bytes_val:.2f} {units[unit_idx]}"


def format_expire(timestamp: int | None) -> str:
    """格式化到期时间"""
    if not timestamp:
        return "无"
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return "无"


def get_status(flow_info: dict[str, int] | None) -> str:
    """获取状态"""
    if not flow_info:
        return "❓ 无信息"

    total = flow_info.get('total', 0)
    used = flow_info.get('upload', 0) + flow_info.get('download', 0)
    expire = flow_info.get('expire')

    if expire and expire < time.time():
        return "❌ 已过期"
    if total > 0 and used >= total:
        return "❌ 流量用完"
    if expire and expire - time.time() < 7 * 24 * 3600:
        return "⚠️ 即将到期"
    return "✅ 正常"


def download_subscription(
    url: str, user_agent: str, timeout: int = 30, max_retries: int = 3
) -> tuple[str, dict[str, int] | None]:
    """下载订阅内容，带重试机制"""
    headers = {"User-Agent": user_agent}
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                log(f"    重试 ({attempt}/{max_retries})...")
                time.sleep(2)  # 重试前等待2秒

            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            content = response.text

            try:
                cleaned = content.strip().replace(" ", "").replace("\n", "").replace("\r", "")
                if cleaned and re.match(r'^[A-Za-z0-9+/=]+$', cleaned):
                    # 补全 base64 padding
                    padding = 4 - len(cleaned) % 4
                    if padding != 4:
                        cleaned += "=" * padding
                    decoded = base64.b64decode(cleaned)
                    content = decoded.decode("utf-8")
            except Exception as e:
                log(f"    ⚠ Base64 解码失败，将作为明文处理: {e}")

            return content, parse_flow_info(response.headers)

        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < max_retries:
                continue
            break

    raise last_error if last_error else Exception("下载失败")


def validate_subscription_content(content: str, sub_name: str) -> tuple[bool, str]:
    """验证订阅内容是否有效"""
    # 检查是否为空
    if not content or len(content.strip()) < 10:
        return False, "内容为空或过短"

    # 检查是否为HTML错误页面
    content_lower = content.lower().strip()
    if content_lower.startswith('<!doctype') or content_lower.startswith('<html'):
        # 检查是否是错误页面
        error_indicators = ['404', 'not found', 'error', 'access denied', 'forbidden', 'captcha', '验证码']
        if any(indicator in content_lower for indicator in error_indicators):
            return False, "返回HTML错误页面"
        # 可能只是重定向到网页，尝试解析
        if '<!doctype html>' in content_lower or '<html' in content_lower:
            return False, "返回的是网页而非订阅内容"

    # 检查是否为有效配置（至少有一些关键字）
    valid_indicators = ['proxies', 'proxy-providers', 'proxy-groups', 'servers', 'outbounds', 'endpoints', 'vmess', 'trojan', 'ssid', 'wireguard']
    if not any(indicator in content_lower for indicator in valid_indicators):
        return False, "内容不包含有效的订阅配置"

    return True, "有效"


def parse_subscriptions() -> list[dict[str, str]]:
    """解析订阅配置 — 动态发现所有 SUB_URL / SUB_URL_N 环境变量"""
    subscriptions: list[dict[str, str]] = []
    sub_keys = sorted(
        [k for k in os.environ if re.match(r'^SUB_URL(_\d+)?$', k)],
        key=lambda k: (0, 0) if k == 'SUB_URL' else (1, int(k.split('_')[-1]))
    )
    for env_name in sub_keys:
        value = os.environ[env_name].strip()
        if not value:
            continue
        if "|" in value:
            name, url = value.split("|", 1)
            name, url = name.strip(), url.strip()
        else:
            url = value
            name = extract_name_from_url(url)
        if name and url:
            subscriptions.append({"name": name, "url": url, "filename": f"{name}.yaml"})
    return subscriptions


def extract_name_from_url(url: str) -> str:
    try:
        domain = urlparse(url).netloc.replace("www.", "").split(":")[0]
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', domain)
        return name[:50]
    except Exception:
        return f"sub_{int(time.time())}"


def upload_to_gist(github_token: str, gist_id: str, files: dict[str, str]) -> str:
    headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
    gist_files = {filename: {"content": content} for filename, content in files.items()}

    try:
        if not gist_id:
            log("    创建新的 Gist...")
            response = requests.post("https://api.github.com/gists", headers=headers, json={
                "description": "SubDl Subscriptions", "public": False, "files": gist_files
            }, timeout=30)
            response.raise_for_status()
            new_id: str = response.json()["id"]
            log(f"    ✓ 创建成功，Gist ID: {new_id}")
            return new_id

        log(f"    更新 Gist: {gist_id}")
        response = requests.patch(f"https://api.github.com/gists/{gist_id}", headers=headers, json={"files": gist_files}, timeout=30)
        response.raise_for_status()
        log("    ✓ 更新成功")
        return gist_id
    except requests.exceptions.HTTPError as e:
        log(f"    ✗ Gist API 错误: {e}")
        log(f"      响应: {e.response.text if hasattr(e, 'response') else 'N/A'}")
        raise
    except Exception as e:
        log(f"    ✗ Gist 上传异常: {e}")
        raise


def parse_cron_interval() -> str:
    """从 workflow 文件解析 cron 间隔"""
    try:
        with open(WORKFLOW_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            match = re.search(r"cron:\s*['\"](\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)['\"]", content)
            if match:
                minute, hour, day, month, weekday = match.groups()
                if minute != '*' and hour == '*':
                    return "每小时"
                elif minute == '*' and hour == '*':
                    return "每分钟"
                elif hour.startswith('*/'):
                    return f"每 {hour[2:]} 小时"
    except Exception as e:
        log(f"  ⚠ cron 解析失败: {e}，使用默认值")
    return "每小时"


def generate_readme(subscription_info: list[dict[str, Any]]) -> str:
    """生成 README 内容"""
    interval = parse_cron_interval()
    lines = [
        "# SubDl", "",
        f"> 最后更新: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S CST')}", "",
        "## 订阅状态", "",
        "| 订阅 | 总流量 | 已用 | 剩余 | 到期时间 | 状态 | 节点数 |",
        "|------|--------|------|------|----------|------|--------|",
    ]

    total_nodes = 0
    for info in subscription_info:
        flow = info.get('flow', {})
        total = flow.get('total', 0)
        used = flow.get('upload', 0) + flow.get('download', 0)
        node_count = info.get('node_count', 0)
        total_nodes += node_count
        lines.append(f"| {info['name']} | {format_bytes(total)} | {format_bytes(used)} | {format_bytes(total - used if total > 0 else 0)} | {format_expire(flow.get('expire'))} | {get_status(flow)} | {node_count} |")

    lines.append(f"| **合计** | | | | | | **{total_nodes}** |")

    lines.extend([
        "", "## 快速配置", "",
        "1. Fork 本仓库",
        "2. 在 Settings → Secrets → Actions 中添加:",
        "   - `GH_TOKEN`: GitHub Token (需要 gist 权限)",
        "   - `GIST_ID`: Gist ID（可选，首次运行后会自动创建并输出）",
        "   - `SUB_URL`: 订阅链接 (`名称|URL` 格式)",
        "   - `SUB_URL_1`, `SUB_URL_2`...: 更多订阅（可选）",
        "3. 在 Actions → Subscriptions Update 中点击 Run workflow", "",
        "## 说明", "",
        f"- {interval}自动更新订阅",
        "- 订阅内容上传到 Gist，不保存在仓库",
        "- `sing-box-config.json` 是可直接使用的完整sing-box配置文件",
        "- 参考 [sub-store](https://github.com/sub-store-org/Sub-Store) 实现", "",
        "## 🚀 sing-box 路由规则集", "",
        "本项目自动将多种格式的规则源（Clash、Surge 等）转换为 sing-box 支持的规则集格式，支持：", "",
        "- 🔄 **自动更新**：每 6 小时自动抓取最新规则并重新生成",
        "- 📦 **双格式输出**：同时生成 JSON 格式（`ruleset/json/`）和 SRS 二进制格式（`ruleset/srs/`）",
        "- ⚡ **性能优化**：SRS 格式加载更快，推荐使用",
        "- 🛠️ **自定义规则**：修改 `ruleset/custom_rule/` 目录下的文件即可添加个人规则", "",
        "### 自定义规则", "",
        "| 文件 | 用途 |",
        "|------|------|",
        "| `custom_direct.list` | 直连域名/IP |",
        "| `custom_proxy.list` | 代理域名/IP |",
        "| `custom_block.list` | 屏蔽域名/IP |",
        "| `custom_whitelist.list` | 白名单（强制直连） |", "",
    ])
    return "\n".join(lines)


def convert_to_singbox(clash_content: str) -> dict[str, Any] | None:
    """将Clash配置转换为Sing-box格式"""
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            f.write(clash_content)
            temp_file = f.name
        try:
            result = subprocess.run(
                ['node', CONVERT_SCRIPT, 'convert', temp_file],
                capture_output=True, text=True, encoding='utf-8'
            )
            if result.returncode != 0:
                log(f"  ✗ 转换失败 (exit {result.returncode}): {result.stderr}")
                return None
            stdout = result.stdout.strip()
            if not stdout:
                log(f"  ✗ 转换输出为空 (stderr: {result.stderr.strip() or '(无)'})")
                return None
            return json.loads(stdout)
        finally:
            os.unlink(temp_file)
    except Exception as e:
        log(f"  ✗ 转换异常: {e}")
        return None


def merge_singbox_config(
    subs_nodes_dict: dict[str, list[dict[str, Any]]], template_path: str | None = None
) -> dict[str, Any] | None:
    """将多个sing-box订阅节点合并到配置模板"""
    try:
        if template_path is None:
            template_path = TEMPLATE_BASE
        if not os.path.exists(template_path):
            log(f"  ✗ 配置模板不存在: {template_path}")
            return None

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(subs_nodes_dict, f)
            sub_temp_file = f.name

        try:
            result = subprocess.run(
                ['python', MERGE_SCRIPT, template_path, sub_temp_file],
                capture_output=True, text=True, encoding='utf-8'
            )
            if result.returncode != 0:
                log(f"  ✗ 合并失败: {result.stderr}")
                return None
            stdout = result.stdout.strip()
            if not stdout:
                log("  ✗ 合并脚本没有输出")
                return None
            return json.loads(stdout)
        finally:
            os.unlink(sub_temp_file)
    except Exception as e:
        log(f"  ✗ 合并异常: {e}")
        return None


# ========== 模板生成 — 通用抽象 ==========

def _generate_template_variant(suffix: str, label: str, transform_fn: Callable[[dict[str, Any]], None]) -> str | None:
    """通用模板变体生成器"""
    try:
        output_path = os.path.join(TEMPLATE_DIR, f'sing-box_template_{suffix}.jsonc')
        template = load_jsonc(TEMPLATE_BASE)
        transform_fn(template)
        output_content = json.dumps(template, indent=2, ensure_ascii=False)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_content)
        log(f"  ✓ 已生成 {label} 模板: config_template/sing-box_template_{suffix}.jsonc")
        return output_content
    except Exception as e:
        log(f"  ✗ 生成 {label} 模板异常: {e}")
        return None


def _generate_all_template_variants() -> None:
    """生成所有模板变体（noTun / tproxy / tun_for_win）"""
    log("→ 生成不含 tun inbound 的模板...")
    _generate_template_variant('noTun', 'noTun', lambda t: _remove_tun_inbounds(t))

    log("→ 生成 tproxy inbound 的模板...")
    _generate_template_variant('tproxy', 'tproxy', lambda t: _replace_tun_with_tproxy(t))

    log("→ 生成适用于 Windows 的 tun 模板...")
    _generate_template_variant('tun_for_win', 'tun_for_win', lambda t: _remove_auto_redirect(t))


def _remove_tun_inbounds(template: dict[str, Any]) -> None:
    """移除所有 type=tun 的 inbound"""
    if 'inbounds' in template and isinstance(template['inbounds'], list):
        original_count = len(template['inbounds'])
        template['inbounds'] = [
            inbound for inbound in template['inbounds']
            if not (isinstance(inbound, dict) and inbound.get('type') == 'tun')
        ]
        removed_count = original_count - len(template['inbounds'])
        log(f"  ✓ 已移除 {removed_count} 个 tun inbound")


def _replace_tun_with_tproxy(template: dict[str, Any]) -> None:
    """将第一个 type=tun inbound 替换为 tproxy"""
    tproxy_inbound: dict[str, object] = {
        "type": "tproxy", "tag": "tproxy-in", "listen": "::", "listen_port": 1536
    }
    if 'inbounds' in template and isinstance(template['inbounds'], list):
        for i, inbound in enumerate(template['inbounds']):
            if isinstance(inbound, dict) and inbound.get('type') == 'tun':
                template['inbounds'][i] = tproxy_inbound
                log("  ✓ 已将 tun inbound 替换为 tproxy inbound")
                break


def _remove_auto_redirect(template: dict[str, Any]) -> None:
    """删除 tun inbound 中的 auto_redirect 字段"""
    if 'inbounds' in template and isinstance(template['inbounds'], list):
        for i, inbound in enumerate(template['inbounds']):
            if isinstance(inbound, dict) and inbound.get('type') == 'tun':
                if 'auto_redirect' in template['inbounds'][i]:
                    del template['inbounds'][i]['auto_redirect']
                    log("  ✓ 已在 tun inbound 中删除 auto_redirect 字段")
                break


# ========== 模板遍历 — 共享抽象 ==========

def _process_templates(
    process_fn: Callable[[str, str], tuple[str, str] | None]
) -> dict[str, str]:
    """遍历所有模板文件，对每个模板执行 process_fn(template_path, base_name)"""
    templates = discover_template_files(TEMPLATE_DIR)
    if not templates:
        log("  ✗ 模板目录中没有找到模板文件")
        return {}

    log(f"  找到 {len(templates)} 个模板文件")

    results: dict[str, str] = {}
    for template_path, base_name in templates:
        entry = process_fn(template_path, base_name)
        if entry:
            filename, content = entry
            results[filename] = content
    return results


def merge_all_templates(subs_nodes_dict: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """遍历所有模板文件并生成配置文件"""
    def _merge_one(template_path: str, base_name: str) -> tuple[str, str] | None:
        template_file = os.path.basename(template_path)
        config_filename = base_name.replace('template', 'config') + '.json'
        log(f"  → 处理模板: {template_file}")
        merged_config = merge_singbox_config(subs_nodes_dict, template_path)
        if merged_config:
            content = json.dumps(merged_config, indent=2, ensure_ascii=False)
            total_nodes = sum(len(nodes) for nodes in subs_nodes_dict.values())
            log(f"    ✓ 生成 {config_filename} ({len(content)} 字节, {total_nodes} 个节点)")
            return config_filename, content
        return None

    return _process_templates(_merge_one)


def generate_provider_configs(sub_url_map: dict[str, str]) -> dict[str, str]:
    """生成 providers 版本的配置文件（直接填充 url，不做其他处理）"""
    def _fill_one(template_path: str, base_name: str) -> tuple[str, str] | None:
        template_file = os.path.basename(template_path)
        if template_file.endswith('.jsonc'):
            template = load_jsonc(template_path)
        else:
            with open(template_path, 'r', encoding='utf-8') as f:
                template = json.load(f)

        filled_count = 0
        for provider in template.get('providers', []):
            provider_tag = provider.get('tag', '')
            if provider_tag in sub_url_map:
                provider['url'] = sub_url_map[provider_tag]
                filled_count += 1

        if filled_count > 0:
            config_filename = base_name.replace('template', 'with_providers_config') + '.json'
            log(f"  → 处理模板: {template_file} -> {config_filename} ({filled_count} 个 providers 已填充)")
            return config_filename, json.dumps(template, indent=2, ensure_ascii=False)
        return None

    return _process_templates(_fill_one)


# ========== 订阅下载 ==========

def _process_subscription(sub: dict[str, str], user_agent: str) -> dict[str, Any]:
    """处理单个订阅（下载 + 验证 + 转换），返回结果字典"""
    result: dict[str, Any] = {"name": sub["name"], "status": "ok"}
    try:
        content, flow_info = download_subscription(sub["url"], user_agent)
        result["flow"] = flow_info

        is_valid, reason = validate_subscription_content(content, sub['name'])
        if not is_valid:
            result["status"] = "invalid"
            result["reason"] = reason
            result["filename"] = None
            return result

        result["raw_content"] = content

        singbox_config = convert_to_singbox(content)
        if singbox_config:
            singbox_nodes = singbox_config.get('outbounds', []) + singbox_config.get('endpoints', [])
            result["node_count"] = len(singbox_nodes)
            result["singbox_nodes"] = singbox_nodes
            result["singbox_content"] = json.dumps(singbox_config, indent=2, ensure_ascii=False)
        else:
            result["status"] = "convert_failed"
            result["reason"] = "转换失败"
    except Exception as e:
        result["status"] = "error"
        result["reason"] = str(e)
    return result


def _download_all_subscriptions(
    subscriptions: list[dict[str, str]], user_agent: str
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """并行下载所有订阅，返回 (files, subscription_info, subs_nodes_dict)"""
    files: dict[str, str] = {}
    subscription_info: list[dict[str, Any]] = []
    subs_nodes_dict: dict[str, list[dict[str, Any]]] = {}
    skipped_subs: list[dict[str, str]] = []

    log(f"→ 并行下载 {len(subscriptions)} 个订阅...")
    with ThreadPoolExecutor(max_workers=len(subscriptions)) as executor:
        future_to_sub = {
            executor.submit(_process_subscription, sub, user_agent): sub
            for sub in subscriptions
        }
        for future in as_completed(future_to_sub):
            sub = future_to_sub[future]
            result = future.result()
            name: str = result["name"]
            status: str = result["status"]
            flow_info: dict[str, int] | None = result.get("flow")
            reason: str = result.get("reason", "未知")
            node_count: int = result.get("node_count", 0)

            if status == "ok":
                raw_content: str = result.get("raw_content", "")
                files[sub["filename"]] = raw_content
                singbox_content: str | None = result.get("singbox_content")
                singbox_nodes: list[dict[str, Any]] | None = result.get("singbox_nodes")

                if singbox_content and singbox_nodes is not None:
                    files[f"{name}-singbox.json"] = singbox_content
                    log(f"  ✓ {name}: 转换成功 ({len(singbox_content)} 字节, {node_count} 个节点)")
                    if node_count > 0:
                        subs_nodes_dict[name] = singbox_nodes
                        log(f"    → '{name}': {node_count} 个节点")
                    else:
                        skipped_subs.append({"name": name, "reason": "节点列表为空"})
                else:
                    log(f"  ⚠️ {name}: 转换失败")
                    skipped_subs.append({"name": name, "reason": "转换失败"})
                subscription_info.append({"name": name, "flow": flow_info, "node_count": node_count, "status": "ok"})
            elif status == "invalid":
                log(f"  ⚠️ {name}: 内容无效 - {reason}")
                subscription_info.append({"name": name, "flow": flow_info, "node_count": 0, "status": "invalid"})
                skipped_subs.append({"name": name, "reason": reason})
            elif status == "convert_failed":
                log(f"  ⚠️ {name}: {reason}")
                skipped_subs.append({"name": name, "reason": reason})
                subscription_info.append({"name": name, "flow": flow_info, "node_count": 0, "status": "ok"})
            else:
                log(f"  ✗ {name}: {reason}")
                subscription_info.append({"name": name, "flow": flow_info, "node_count": 0, "status": "error"})
                skipped_subs.append({"name": name, "reason": reason})

    if skipped_subs:
        log(f"⚠️ {len(skipped_subs)} 个订阅跳过: {', '.join([s['name'] for s in skipped_subs])}")

    return files, subscription_info, subs_nodes_dict


# ========== 配置生成与上传 ==========

def _generate_and_upload(
    files: dict[str, str],
    subs_nodes_dict: dict[str, list[dict[str, Any]]],
    subscriptions: list[dict[str, str]],
    subscription_info: list[dict[str, Any]],
    github_token: str,
    gist_id: str,
) -> str:
    """生成合并配置、providers 配置，并上传到 Gist"""

    merged_configs = merge_all_templates(subs_nodes_dict)
    for filename, content in merged_configs.items():
        files[filename] = content
    if merged_configs:
        log(f"  ✓ 共生成 {len(merged_configs)} 个配置文件")

    log("→ 生成 providers 版本配置文件...")
    sub_url_map = {sub['name']: sub['url'] for sub in subscriptions}
    provider_configs = generate_provider_configs(sub_url_map)
    for filename, content in provider_configs.items():
        files[filename] = content
    if provider_configs:
        log(f"  ✓ 共生成 {len(provider_configs)} 个 providers配置文件")

    readme_content = generate_readme(subscription_info)
    with open("README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)
    log("✓ README 已更新")

    log(f"上传 {len(files)} 个文件到 Gist...")
    new_gist_id = upload_to_gist(github_token, gist_id, files)

    if new_gist_id != gist_id:
        log(f"重要提示: 已创建新的 Gist ID: {new_gist_id}")
        log("请在 Repository secrets 中设置 GIST_ID")

    return new_gist_id


# ========== 主入口 ==========

def main() -> None:
    log("=" * 60, file=sys.stdout)
    log("SubDl - Subscription Downloader", file=sys.stdout)
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", file=sys.stdout)
    log("=" * 60, file=sys.stdout)

    github_token = get_env_var("GH_TOKEN", required=True)
    gist_id = get_env_var("GIST_ID", default="")
    user_agent = get_env_var("USER_AGENT", default="clash-verge/v2.4.4")

    _generate_all_template_variants()

    subscriptions = parse_subscriptions()
    if not subscriptions:
        log("错误: 未找到订阅配置")
        sys.exit(1)

    log(f"找到 {len(subscriptions)} 个订阅")

    files, subscription_info, subs_nodes_dict = _download_all_subscriptions(subscriptions, user_agent)

    valid_count = len(files)
    if valid_count == 0:
        log("错误: 所有订阅下载失败或内容无效")
        sys.exit(1)
    log(f"✓ 有效订阅: {valid_count}/{len(subscriptions)}")

    if not subs_nodes_dict:
        log("✗ 错误: 没有有效的订阅节点，将不上传配置文件")
        sys.exit(1)
    log(f"✓ 合并节点: {len(subs_nodes_dict)}/{len(subscriptions)}")

    _generate_and_upload(files, subs_nodes_dict, subscriptions, subscription_info, github_token, gist_id)

    log(f"完成! 成功处理 {len(files)} 个订阅")


if __name__ == "__main__":
    main()