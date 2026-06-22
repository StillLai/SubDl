#!/usr/bin/env python3
"""
订阅下载、转换、合并与上传模块

主流程：
  1. 解析环境变量获取订阅列表
  2. 并行下载 + 验证（不做转换）
  3. 批量转换（单次 Node.js 进程）
  4. 合并配置、生成 README、上传 Gist
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Any

from utils import (
    logger, load_jsonc, discover_template_files,
    http_get_with_retry, http_request, try_decode_base64,
    FlowInfo, Subscription, DownloadResult, SubscriptionInfo,
    format_bytes, format_expire, get_env_var,
    PROJECT_ROOT, TEMPLATE_DIR, WORKFLOW_PATH,
    CONVERT_SCRIPT, TEMPLATE_BASE, USER_AGENT,
)
from merge_config import merge_config


# ========== 流量解析 ==========

_FLOW_KEY_PATTERN: re.Pattern[str] = re.compile(r'(\w+)=(\d+)')


def parse_flow_info(headers: dict[str, str]) -> FlowInfo | None:
    """从响应头解析流量信息"""
    flow_header = headers.get('subscription-userinfo', '')
    if not flow_header:
        return None

    upload = download = total = 0
    expire: int | None = None
    for match in _FLOW_KEY_PATTERN.finditer(flow_header):
        key, value = match.group(1), int(match.group(2))
        if key == 'upload':
            upload = value
        elif key == 'download':
            download = value
        elif key == 'total':
            total = value
        elif key == 'expire':
            expire = value

    return FlowInfo(upload=upload, download=download, total=total, expire=expire)


# ========== 订阅验证 ==========

_VALID_INDICATORS: tuple[str, ...] = (
    'proxies', 'proxy-providers', 'proxy-groups', 'servers',
    'outbounds', 'endpoints', 'vmess', 'trojan', 'ssid', 'wireguard',
)


def _validate_subscription(content: str) -> str | None:
    """验证订阅内容，返回失败原因或 None（有效）"""
    if not content or len(content.strip()) < 10:
        return "内容为空或过短"

    content_lower = content.lower().strip()
    if content_lower.startswith('<!doctype') or content_lower.startswith('<html'):
        return "返回的是网页而非订阅内容"

    if not any(indicator in content_lower for indicator in _VALID_INDICATORS):
        return "内容不包含有效的订阅配置"

    return None


# ========== 订阅解析 ==========

def _parse_single_entry(value: str) -> Subscription | None:
    """解析单个订阅条目（名称|URL 或纯 URL）"""
    if "|" in value:
        name, url = value.split("|", 1)
        name, url = name.strip(), url.strip()
    else:
        url = value.strip()
        name = None
    return Subscription.from_url(url, name) if url else None


def parse_subscriptions() -> list[Subscription]:
    """解析订阅配置 — 从 SUB_URL / SUB_URL_N 环境变量获取"""
    subscriptions: list[Subscription] = []

    sub_keys = sorted(
        (k for k in os.environ if re.match(r'^SUB_URL(_\d+)?$', k)),
        key=lambda k: -1 if k == 'SUB_URL' else int(k.split('_')[-1])
    )
    for env_name in sub_keys:
        value = os.environ[env_name].strip()
        if not value:
            continue
        sub = _parse_single_entry(value)
        if sub:
            subscriptions.append(sub)

    return subscriptions


# ========== 订阅下载 ==========

def _download_subscription(sub: Subscription, user_agent: str) -> DownloadResult:
    """下载单个订阅，返回结果"""
    try:
        headers = {"User-Agent": user_agent}
        response = http_get_with_retry(sub.url, headers=headers)
        content = response.text

        # 尝试 Base64 解码
        decoded = try_decode_base64(content)
        if decoded is not content:
            logger.debug(f"    {sub.name}: 内容已从 Base64 解码")
            content = decoded

        flow = parse_flow_info(response.headers)
        reason = _validate_subscription(content)
        if reason:
            return DownloadResult(name=sub.name, status="invalid", flow=flow, reason=reason, filename=sub.filename)

        return DownloadResult(name=sub.name, status="ok", flow=flow, filename=sub.filename, raw_content=content)

    except Exception as e:
        return DownloadResult(name=sub.name, status="error", reason=str(e), filename=sub.filename)


def _download_all(subscriptions: list[Subscription], user_agent: str) -> list[DownloadResult]:
    """并行下载所有订阅"""
    logger.info(f"→ 并行下载 {len(subscriptions)} 个订阅...")
    results: list[DownloadResult] = []

    with ThreadPoolExecutor(max_workers=min(len(subscriptions), 8)) as executor:
        futures = {
            executor.submit(_download_subscription, sub, user_agent): sub
            for sub in subscriptions
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if result.is_success:
                logger.debug(f"  ✓ {result.name}: 下载成功")
            else:
                logger.warn(f"  ✗ {result.name}: {result.reason}")

    return results


# ========== 批量转换 ==========

def _convert_batch(contents_dict: dict[str, str]) -> dict[str, dict[str, Any] | None]:
    """批量转换：将所有 Clash 内容一次性传给单个 Node.js 进程
    
    Args:
        contents_dict: {订阅名称: clash内容, ...}
    
    Returns:
        {订阅名称: singbox配置dict | None, ...} — None 表示转换失败
    """
    if not contents_dict:
        return {}

    batch_input_file: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, encoding='utf-8'
        ) as f:
            json.dump(contents_dict, f, ensure_ascii=False)
            batch_input_file = f.name

        logger.info(f"  → 批量转换 {len(contents_dict)} 个订阅（单进程）...")
        result = subprocess.run(
            ['node', str(CONVERT_SCRIPT), 'batch-convert', batch_input_file],
            capture_output=True, text=True, encoding='utf-8'
        )

        if result.returncode != 0:
            logger.error(f"  批量转换失败 (exit {result.returncode}): {result.stderr}")
            return {name: None for name in contents_dict}

        stdout = result.stdout.strip()
        if not stdout:
            logger.error(f"  批量转换输出为空 (stderr: {result.stderr.strip() or '(无)'})")
            return {name: None for name in contents_dict}

        raw_result = json.loads(stdout)
        return {
            name: val if isinstance(val, dict) else None
            for name, val in ((n, raw_result.get(n)) for n in contents_dict)
        }

    except Exception as e:
        logger.error(f"  批量转换异常: {e}")
        return {name: None for name in contents_dict}
    finally:
        if batch_input_file:
            os.unlink(batch_input_file)


# ========== 结果聚合 ==========

def _aggregate_results(
    download_results: list[DownloadResult],
) -> tuple[dict[str, str], list[SubscriptionInfo], dict[str, list[dict[str, Any]]]]:
    """聚合下载结果：批量转换 → 收集文件、订阅信息和节点字典"""
    # 收集需要转换的内容
    contents_to_convert = {
        r.name: r.raw_content
        for r in download_results
        if r.is_success and r.raw_content
    }
    converted = _convert_batch(contents_to_convert) if contents_to_convert else {}

    files: dict[str, str] = {}
    subscription_info: list[SubscriptionInfo] = []
    subs_nodes_dict: dict[str, list[dict[str, Any]]] = {}
    skipped: list[str] = []

    for result in download_results:
        if not result.is_success:
            skipped.append(f"{result.name} ({result.reason})")
            subscription_info.append(SubscriptionInfo(
                name=result.name, flow=result.flow, node_count=0, status=result.status
            ))
            continue

        # 保存原始内容
        if result.raw_content is None:
            skipped.append(f"{result.name} (原始内容为空)")
            continue
        files[result.filename] = result.raw_content

        # 处理转换结果
        singbox_config = converted.get(result.name)
        if singbox_config is None:
            skipped.append(f"{result.name} (转换失败)")
            subscription_info.append(SubscriptionInfo(
                name=result.name, flow=result.flow, node_count=0, status="convert_failed"
            ))
            continue

        singbox_nodes = singbox_config.get('outbounds', []) + singbox_config.get('endpoints', [])
        node_count = len(singbox_nodes)
        singbox_content = json.dumps(singbox_config, indent=2, ensure_ascii=False)
        files[f"{result.name}-singbox.json"] = singbox_content

        if node_count > 0:
            subs_nodes_dict[result.name] = singbox_nodes
            logger.info(f"  ✓ {result.name}: 转换成功 ({len(singbox_content)} 字节, {node_count} 个节点)")
        else:
            skipped.append(f"{result.name} (空节点)")

        subscription_info.append(SubscriptionInfo(
            name=result.name, flow=result.flow, node_count=node_count, status="ok"
        ))

    if skipped:
        logger.warn(f"⚠️ {len(skipped)} 个订阅跳过: {', '.join(skipped)}")

    return files, subscription_info, subs_nodes_dict


# ========== 模板生成 ==========

def _get_inbounds(template: dict[str, Any]) -> list[dict[str, Any]] | None:
    """获取模板的 inbounds 列表"""
    inbounds = template.get('inbounds')
    return inbounds if isinstance(inbounds, list) else None


def _remove_tun_inbounds(template: dict[str, Any]) -> None:
    """移除所有 type=tun 的 inbound"""
    inbounds = _get_inbounds(template)
    if inbounds is None:
        return
    original = len(inbounds)
    template['inbounds'] = [
        ib for ib in inbounds
        if not (isinstance(ib, dict) and ib.get('type') == 'tun')
    ]
    logger.debug(f"  已移除 {original - len(template['inbounds'])} 个 tun inbound")


def _replace_tun_with_tproxy(template: dict[str, Any]) -> None:
    """将第一个 type=tun inbound 替换为 tproxy"""
    inbounds = _get_inbounds(template)
    if inbounds is None:
        return
    for i, ib in enumerate(inbounds):
        if isinstance(ib, dict) and ib.get('type') == 'tun':
            inbounds[i] = {"type": "tproxy", "tag": "tproxy-in", "listen": "::", "listen_port": 1536}
            logger.debug("  已将 tun inbound 替换为 tproxy inbound")
            break


def _remove_auto_redirect(template: dict[str, Any]) -> None:
    """删除 tun inbound 中的 auto_redirect 字段"""
    inbounds = _get_inbounds(template)
    if inbounds is None:
        return
    for ib in inbounds:
        if isinstance(ib, dict) and ib.get('type') == 'tun':
            if 'auto_redirect' in ib:
                del ib['auto_redirect']
                logger.debug("  已在 tun inbound 中删除 auto_redirect 字段")
            break


def _generate_template_variant(
    suffix: str, label: str, transform: Callable[[dict[str, Any]], None],
    base_template: dict[str, Any]
) -> None:
    """生成单个模板变体"""
    try:
        output_path = TEMPLATE_DIR / f'sing-box_template_{suffix}.jsonc'
        template = copy.deepcopy(base_template)
        transform(template)
        output_content = json.dumps(template, indent=2, ensure_ascii=False)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_content)
        logger.debug(f"  ✓ 已生成 {label} 模板")
    except Exception as e:
        logger.error(f"  ✗ 生成 {label} 模板异常: {e}")


def generate_all_template_variants() -> None:
    """生成所有模板变体（noTun / tproxy / tun_for_win）"""
    base_template = load_jsonc(TEMPLATE_BASE)
    for suffix, label, transform in [
        ('noTun', 'noTun', _remove_tun_inbounds),
        ('tproxy', 'tproxy', _replace_tun_with_tproxy),
        ('tun_for_win', 'tun_for_win', _remove_auto_redirect),
    ]:
        _generate_template_variant(suffix, label, transform, base_template)


# ========== 遍历模板生成配置 ==========

def _process_templates(
    process_fn: Callable[[str, str], tuple[str, str] | None]
) -> dict[str, str]:
    """遍历所有模板文件，并行处理"""
    templates = discover_template_files(TEMPLATE_DIR)
    if not templates:
        logger.error("  模板目录中没有找到模板文件")
        return {}

    logger.debug(f"  找到 {len(templates)} 个模板文件")
    results: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=min(len(templates), 4)) as executor:
        futures = {
            executor.submit(process_fn, path, name): (path, name)
            for path, name in templates
        }
        for future in as_completed(futures):
            entry = future.result()
            if entry:
                filename, content = entry
                results[filename] = content

    return results


def merge_all_templates(subs_nodes_dict: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """遍历所有模板文件并生成合并配置"""
    def _merge_one(template_path: str, base_name: str) -> tuple[str, str] | None:
        config_filename = base_name.replace('template', 'config') + '.json'
        logger.info(f"  → 处理模板: {os.path.basename(template_path)}")
        merged = merge_singbox_config(subs_nodes_dict, template_path)
        if merged:
            content = json.dumps(merged, indent=2, ensure_ascii=False)
            total_nodes = sum(len(nodes) for nodes in subs_nodes_dict.values())
            logger.info(f"    ✓ 生成 {config_filename} ({len(content)} 字节, {total_nodes} 个节点)")
            return config_filename, content
        return None

    return _process_templates(_merge_one)


def generate_provider_configs(sub_url_map: dict[str, str]) -> dict[str, str]:
    """生成 providers 版本的配置文件"""
    def _fill_one(template_path: str, base_name: str) -> tuple[str, str] | None:
        template = load_jsonc(template_path)
        filled = 0
        for provider in template.get('providers', []):
            tag = provider.get('tag', '')
            if tag in sub_url_map:
                provider['url'] = sub_url_map[tag]
                filled += 1

        if filled > 0:
            config_filename = base_name.replace('template', 'with_providers_config') + '.json'
            logger.debug(f"  → {os.path.basename(template_path)} -> {config_filename} ({filled} 个 providers)")
            return config_filename, json.dumps(template, indent=2, ensure_ascii=False)
        return None

    return _process_templates(_fill_one)


# ========== 配置合并入口 ==========

def merge_singbox_config(
    subs_nodes_dict: dict[str, list[dict[str, Any]]], template_path: str | None = None
) -> dict[str, Any] | None:
    """将订阅节点合并到配置模板"""
    if template_path is None:
        template_path = str(TEMPLATE_BASE)
    if not os.path.exists(template_path):
        logger.error(f"  配置模板不存在: {template_path}")
        return None

    try:
        template = load_jsonc(template_path)
        return merge_config(template, subs_nodes_dict)
    except Exception as e:
        logger.error(f"  合并异常: {e}")
        return None


# ========== cron 解析 ==========

def _parse_cron_interval() -> str:
    """从 workflow 文件解析 cron 间隔"""
    try:
        content = WORKFLOW_PATH.read_text(encoding='utf-8')
        match = re.search(r"cron:\s*['\"](\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)['\"]", content)
        if match:
            _, hour = match.group(1), match.group(2)
            if hour.startswith('*/'):
                return f"每 {hour[2:]} 小时"
    except Exception as e:
        logger.debug(f"  cron 解析失败: {e}，使用默认值")
    return "每小时"


# ========== README 生成 ==========

def generate_readme(subscription_info: list[SubscriptionInfo]) -> str:
    """生成 README 内容"""
    interval = _parse_cron_interval()
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S CST')

    lines = [
        "# SubDl", "",
        f"> 最后更新: {now}", "",
        "## 订阅状态", "",
        "| 订阅 | 总流量 | 已用 | 剩余 | 到期时间 | 状态 | 节点数 |",
        "|------|--------|------|------|----------|------|--------|",
    ]

    total_nodes = 0
    for info in subscription_info:
        flow = info.flow
        if flow:
            total, used, remaining = flow.total, flow.used, flow.remaining
            expire_str = format_expire(flow.expire)
            status_str = flow.status
        else:
            total, used, remaining = 0, 0, 0
            expire_str, status_str = "无", "❓ 无信息"

        total_nodes += info.node_count
        lines.append(
            f"| {info.name} | {format_bytes(total)} | {format_bytes(used)}"
            f" | {format_bytes(remaining)} | {expire_str}"
            f" | {status_str} | {info.node_count} |"
        )

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
        "- `sing-box-config.json` 是可直接使用的完整 sing-box 配置文件",
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


# ========== Gist 上传 ==========

def upload_to_gist(github_token: str, gist_id: str, files: dict[str, str]) -> str:
    """上传文件到 GitHub Gist"""
    from urllib.error import HTTPError as UrllibHTTPError

    headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
    gist_files = {name: {"content": content} for name, content in files.items()}

    try:
        if not gist_id:
            logger.info("    创建新的 Gist...")
            resp = http_request("POST", "https://api.github.com/gists", headers=headers, json_body={
                "description": "SubDl Subscriptions", "public": False, "files": gist_files
            })
            resp.raise_for_status()
            new_id: str = json.loads(resp.text)["id"]
            logger.info(f"    ✓ 创建成功，Gist ID: {new_id}")
            return new_id

        logger.info(f"    更新 Gist: {gist_id}")
        resp = http_request("PATCH", f"https://api.github.com/gists/{gist_id}", headers=headers, json_body={"files": gist_files})
        resp.raise_for_status()
        logger.info("    ✓ 更新成功")
        return gist_id

    except UrllibHTTPError as e:
        logger.error(f"    Gist API 错误: {e}")
        logger.error(f"      响应: {e.read().decode('utf-8', errors='replace')}")
        raise
    except Exception as e:
        logger.error(f"    Gist 上传异常: {e}")
        raise


# ========== 主流程 ==========

def _generate_and_upload(
    files: dict[str, str],
    subs_nodes_dict: dict[str, list[dict[str, Any]]],
    subscriptions: list[Subscription],
    subscription_info: list[SubscriptionInfo],
    github_token: str,
    gist_id: str,
) -> str:
    """生成合并配置、providers 配置，并上传到 Gist"""

    logger.info("→ 生成合并配置和 providers 配置...")
    sub_url_map = {sub.name: sub.url for sub in subscriptions}

    merged = merge_all_templates(subs_nodes_dict)
    files.update(merged)
    if merged:
        logger.info(f"  ✓ 共生成 {len(merged)} 个配置文件")

    providers = generate_provider_configs(sub_url_map)
    files.update(providers)
    if providers:
        logger.info(f"  ✓ 共生成 {len(providers)} 个 providers 配置文件")

    # 生成 README
    readme_path = PROJECT_ROOT / "README.md"
    readme_content = generate_readme(subscription_info)
    readme_path.write_text(readme_content, encoding="utf-8")
    logger.info("✓ README 已更新")

    # 上传到 Gist
    logger.info(f"上传 {len(files)} 个文件到 Gist...")
    new_gist_id = upload_to_gist(github_token, gist_id, files)

    if new_gist_id != gist_id:
        logger.info(f"重要提示: 已创建新的 Gist ID: {new_gist_id}")
        logger.info("请在 Repository secrets 中设置 GIST_ID")

    return new_gist_id


def main() -> None:
    """主入口"""
    logger.info("=" * 60)
    logger.info("SubDl - Subscription Downloader")
    logger.info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    github_token = get_env_var("GH_TOKEN", required=True)
    gist_id = get_env_var("GIST_ID", default="")

    # 解析订阅配置
    subscriptions = parse_subscriptions()
    if not subscriptions:
        logger.info("错误: 未找到订阅配置")
        sys.exit(1)
    logger.info(f"找到 {len(subscriptions)} 个订阅")

    # 并行执行：模板生成（后台线程）+ 订阅下载（主线程）
    logger.info("::group::Download & Convert subscriptions")
    templates_thread = threading.Thread(target=generate_all_template_variants)
    templates_thread.start()

    download_results = _download_all(subscriptions, USER_AGENT)
    templates_thread.join()
    logger.info("::endgroup::")

    # 检查有效性
    valid_results = [r for r in download_results if r.is_success]
    if not valid_results:
        logger.info("错误: 所有订阅下载失败或内容无效")
        sys.exit(1)
    logger.info(f"✓ 有效订阅: {len(valid_results)}/{len(subscriptions)}")

    # 聚合结果
    files, subscription_info, subs_nodes_dict = _aggregate_results(download_results)

    if not subs_nodes_dict:
        logger.info("✗ 错误: 没有有效的订阅节点，将不上传配置文件")
        sys.exit(1)
    logger.info(f"✓ 合并节点: {len(subs_nodes_dict)}/{len(subscriptions)}")

    # 生成配置并上传
    logger.info("::group::Generate configs & Upload to Gist")
    _generate_and_upload(files, subs_nodes_dict, subscriptions, subscription_info, github_token, gist_id)
    logger.info("::endgroup::")

    logger.info(f"完成! 成功处理 {len(valid_results)} 个订阅，共 {len(files)} 个文件")


if __name__ == "__main__":
    main()