#!/usr/bin/env python3
"""订阅下载、转换、合并与上传模块

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Any

from utils import (
    logger, load_jsonc,
    http_get_with_retry, http_request, try_decode_base64,
    FlowInfo, Subscription, DownloadResult, SubscriptionInfo,
    PROJECT_ROOT, TEMPLATE_DIR, TEMPLATE_BASE,
    CONVERT_SCRIPT, USER_AGENT,
)
from merge_config import merge_config


# ========== 流量解析 ==========

_FLOW_KEY_PATTERN: re.Pattern[str] = re.compile(r'(\w+)=(\d+)')


def parse_flow_info(headers: dict[str, str]) -> FlowInfo | None:
    """从响应头解析流量信息"""
    flow_header = headers.get('subscription-userinfo', '')
    if not flow_header:
        return None

    fields: dict[str, int | None] = {'upload': 0, 'download': 0, 'total': 0, 'expire': None}
    for match in _FLOW_KEY_PATTERN.finditer(flow_header):
        key, value = match.group(1), int(match.group(2))
        if key in fields:
            fields[key] = value

    return FlowInfo(
        upload=fields['upload'] or 0,
        download=fields['download'] or 0,
        total=fields['total'] or 0,
        expire=fields['expire'],
    )


# ========== 订阅解析与验证 ==========

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


def parse_subscriptions() -> list[Subscription]:
    """解析订阅配置 — 从 SUB_URL / SUB_URL_N 环境变量获取"""
    sub_keys = sorted(
        (k for k in os.environ if re.match(r'^SUB_URL(_\d+)?$', k)),
        key=lambda k: -1 if k == 'SUB_URL' else int(k.split('_')[-1])
    )
    subscriptions: list[Subscription] = []
    for env_name in sub_keys:
        value = os.environ[env_name].strip()
        if not value:
            continue
        if "|" in value:
            name, url = value.split("|", 1)
            sub = Subscription.from_url(url.strip(), name.strip())
        else:
            sub = Subscription.from_url(value.strip())
        if sub:
            subscriptions.append(sub)
    return subscriptions


# ========== 订阅下载 ==========

def _download_subscription(sub: Subscription, user_agent: str) -> DownloadResult:
    """下载单个订阅，返回结果"""
    try:
        response = http_get_with_retry(sub.url, headers={"User-Agent": user_agent})
        content = response.text

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
    """批量转换：将所有 Clash 内容一次性传给单个 Node.js 进程"""
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

        if result.raw_content is None:
            skipped.append(f"{result.name} (原始内容为空)")
            continue
        files[result.filename] = result.raw_content

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


# ========== 模板变体生成 ==========

_TEMPLATE_VARIANTS: list[tuple[str, str, str]] = [
    ('noTun', 'noTun', 'remove_tun'),
    ('tproxy', 'tproxy', 'replace_tun_with_tproxy'),
    ('tun_for_win', 'tun_for_win', 'remove_auto_redirect'),
]


def _transform_template(template: dict[str, Any], action: str) -> None:
    """对模板执行指定变换"""
    inbounds = template.get('inbounds', [])
    if not isinstance(inbounds, list):
        return

    if action == 'remove_tun':
        template['inbounds'] = [
            ib for ib in inbounds
            if not (isinstance(ib, dict) and ib.get('type') == 'tun')
        ]
    elif action == 'replace_tun_with_tproxy':
        for i, ib in enumerate(inbounds):
            if isinstance(ib, dict) and ib.get('type') == 'tun':
                inbounds[i] = {"type": "tproxy", "tag": "tproxy-in", "listen": "::", "listen_port": 1536}
                break
    elif action == 'remove_auto_redirect':
        for ib in inbounds:
            if isinstance(ib, dict) and ib.get('type') == 'tun':
                ib.pop('auto_redirect', None)
                break


def generate_all_template_variants() -> None:
    """生成所有模板变体（noTun / tproxy / tun_for_win）"""
    base_template = load_jsonc(TEMPLATE_BASE)
    for suffix, label, action in _TEMPLATE_VARIANTS:
        try:
            template = copy.deepcopy(base_template)
            _transform_template(template, action)
            output_path = TEMPLATE_DIR / f'sing-box_template_{suffix}.jsonc'
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(template, f, indent=2, ensure_ascii=False)
            logger.debug(f"  ✓ 已生成 {label} 模板")
        except Exception as e:
            logger.error(f"  ✗ 生成 {label} 模板异常: {e}")


# ========== 模板文件列表 ==========

# 硬编码模板列表，避免运行时遍历目录
_TEMPLATES: list[tuple[str, str]] = [
    (str(TEMPLATE_BASE), TEMPLATE_BASE.stem),
] + [
    (str(TEMPLATE_DIR / f'sing-box_template_{s}.jsonc'), f'sing-box_template_{s}')
    for s, _, _ in _TEMPLATE_VARIANTS
]


def _process_templates(
    process_fn: Any,
) -> dict[str, str]:
    """遍历所有模板文件，串行处理"""
    logger.debug(f"  处理 {len(_TEMPLATES)} 个模板文件")
    results: dict[str, str] = {}
    for path, name in _TEMPLATES:
        entry = process_fn(path, name)
        if entry:
            results[entry[0]] = entry[1]
    return results


# ========== 配置合并 ==========

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


# ========== README 生成 ==========

def _format_bytes(n: int) -> str:
    """将字节数格式化为人类可读字符串"""
    if n == 0:
        return "0 B"
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    i = min(int(n.bit_length() - 1) // 10, len(units) - 1)
    return f"{n / (1024 ** i):.2f} {units[i]}"


def _format_expire(timestamp: int | None) -> str:
    """格式化过期时间"""
    if not timestamp:
        return "无"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except Exception:
        return "无"


def generate_status_svg(subscription_info: list[SubscriptionInfo]) -> str:
    """生成订阅状态 SVG 图片（用于 GitHub Pages 展示）"""
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S CST')

    def _esc(s: str) -> str:
        """XML 转义"""
        amp = chr(38)  # &
        s = s.replace(amp, amp + "amp;")
        s = s.replace(chr(60), amp + "lt;")
        s = s.replace(chr(62), amp + "gt;")
        return s

    col_widths = [140, 100, 100, 100, 100, 100, 80]
    headers = ['订阅', '总流量', '已用', '剩余', '到期时间', '状态', '节点数']
    row_h = 40
    svg_w = sum(col_widths)
    header_h = 65
    svg_h = header_h + len(subscription_info) * row_h + row_h

    # 表头背景色
    header_bg = '#2d333b'
    # 行背景色交替
    row_colors = ['#ffffff', '#f6f8fa']

    rows = []
    total_nodes = 0
    for info in subscription_info:
        if info.flow:
            f = info.flow
            rows.append([
                _esc(info.name), _format_bytes(f.total), _format_bytes(f.used),
                _format_bytes(f.remaining), _format_expire(f.expire),
                f.status, str(info.node_count),
            ])
        else:
            rows.append([_esc(info.name), '0 B', '0 B', '0 B', '无', '❓ 无信息', str(info.node_count)])
        total_nodes += info.node_count

    # 生成行 SVG
    rows_svg = []
    for i, row in enumerate(rows):
        y = header_h + i * row_h
        bg = row_colors[i % 2]
        rows_svg.append(f'  <rect x="0" y="{y}" width="{svg_w}" height="{row_h}" fill="{bg}"/>')
        x = 0
        for j, cell in enumerate(row):
            cx = x + col_widths[j] // 2
            cy = y + row_h // 2 + 5
            rows_svg.append(
                f'  <text x="{cx}" y="{cy}" text-anchor="middle"'
                f' font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"'
                f' font-size="13" fill="#24292f">{cell}</text>'
            )
            x += col_widths[j]
    # 分隔线
    for i in range(len(rows) + 1):
        y = header_h + i * row_h
        rows_svg.append(f'  <line x1="0" y1="{y}" x2="{svg_w}" y2="{y}" stroke="#d0d7de" stroke-width="1"/>')

    # 表头 SVG
    header_svg = [f'  <rect x="0" y="0" width="{svg_w}" height="{header_h}" fill="{header_bg}"/>']
    header_svg.append(
        f'  <text x="{svg_w // 2}" y="25" text-anchor="middle"'
        f' font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"'
        f' font-size="16" font-weight="bold" fill="#ffffff">SubDl 订阅状态</text>'
    )
    header_svg.append(
        f'  <text x="{svg_w // 2}" y="45" text-anchor="middle"'
        f' font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"'
        f' font-size="11" fill="#8b949e">最后更新: {_esc(now)}</text>'
    )

    # 列标题
    col_header_y = header_h - 8
    x = 0
    for j, h in enumerate(headers):
        cx = x + col_widths[j] // 2
        header_svg.append(
            f'  <text x="{cx}" y="{col_header_y}" text-anchor="middle"'
            f' font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"'
            f' font-size="12" font-weight="bold" fill="#e6edf3">{h}</text>'
        )
        x += col_widths[j]

    # 合计行
    total_y = header_h + len(rows) * row_h
    total_svg = [
        f'  <rect x="0" y="{total_y}" width="{svg_w}" height="{row_h}" fill="#ddf4ff"/>',
        f'  <text x="{svg_w // 2}" y="{total_y + row_h // 2 + 5}" text-anchor="middle"'
        f' font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"'
        f' font-size="13" font-weight="bold" fill="#24292f">合计: {total_nodes} 个节点</text>',
    ]

    # 外边框
    border = [
        f'<rect x="0" y="0" width="{svg_w}" height="{svg_h}" rx="8" ry="8"'
        f' fill="none" stroke="#d0d7de" stroke-width="1"/>'
    ]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}">'
        f'<rect x="0" y="0" width="{svg_w}" height="{svg_h}" rx="8" ry="8" fill="#ffffff"/>',
        *header_svg,
        *rows_svg,
        *total_svg,
        *border,
        '</svg>',
    ]
    return '\n'.join(parts)


# ========== Gist 上传 ==========

def upload_to_gist(github_token: str, gist_id: str, files: dict[str, str]) -> str:
    """上传文件到 GitHub Gist"""
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

    # 生成 SVG 状态图片（供 GitHub Pages 使用）
    svg_content = generate_status_svg(subscription_info)
    svg_output = PROJECT_ROOT / "status.svg"
    svg_output.write_text(svg_content, encoding="utf-8")
    logger.info("✓ 状态 SVG 已生成")

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

    github_token = os.environ.get("GH_TOKEN")
    if not github_token:
        logger.error("环境变量 GH_TOKEN 未设置")
        sys.exit(1)
    gist_id = os.environ.get("GIST_ID", "")

    subscriptions = parse_subscriptions()
    if not subscriptions:
        logger.error("未找到订阅配置")
        sys.exit(1)
    logger.info(f"找到 {len(subscriptions)} 个订阅")

    # 模板变体生成
    logger.info("::group::Download & Convert subscriptions")
    generate_all_template_variants()

    # 并行下载
    download_results = _download_all(subscriptions, USER_AGENT)
    logger.info("::endgroup::")

    valid_results = [r for r in download_results if r.is_success]
    if not valid_results:
        logger.error("所有订阅下载失败或内容无效")
        sys.exit(1)
    logger.info(f"✓ 有效订阅: {len(valid_results)}/{len(subscriptions)}")

    # 聚合结果
    files, subscription_info, subs_nodes_dict = _aggregate_results(download_results)

    if not subs_nodes_dict:
        logger.error("没有有效的订阅节点，将不上传配置文件")
        sys.exit(1)
    logger.info(f"✓ 合并节点: {len(subs_nodes_dict)}/{len(subscriptions)}")

    # 生成配置并上传
    logger.info("::group::Generate configs & Upload to Gist")
    _generate_and_upload(files, subs_nodes_dict, subscriptions, subscription_info, github_token, gist_id)
    logger.info("::endgroup::")

    logger.info(f"完成! 成功处理 {len(valid_results)} 个订阅，共 {len(files)} 个文件")


if __name__ == "__main__":
    main()