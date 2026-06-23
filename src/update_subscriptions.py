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
    """生成订阅状态 SVG 图片（浅色高级感风格）"""
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S CST')

    def _esc(s: str) -> str:
        """XML 转义"""
        amp = chr(38)  # &
        s = s.replace(amp, amp + "amp;")
        s = s.replace(chr(60), amp + "lt;")
        s = s.replace(chr(62), amp + "gt;")
        return s

    def _pct(used: int, total: int) -> float:
        """计算使用百分比"""
        if total <= 0:
            return 0.0
        return min(used / total * 100, 100.0)

    # 配色 - 浅色高级感
    bg_color = '#f0f2f5'
    card_bg = '#ffffff'
    header_bg = '#f8fafc'
    border_color = '#e2e8f0'
    text_primary = '#1e293b'
    text_secondary = '#64748b'
    accent_blue = '#3b82f6'
    accent_green = '#10b981'
    accent_orange = '#f59e0b'
    accent_red = '#ef4444'
    bar_bg = '#e2e8f0'

    # 布局参数
    pad = 20
    card_radius = 12
    row_h = 56
    title_h = 70
    col_header_h = 36

    # 列定义: (名称, 宽度, 对齐)
    cols = [
        ('订阅', 100, 'left'),
        ('流量使用', 280, 'left'),
        ('到期时间', 90, 'center'),
        ('状态', 70, 'center'),
        ('节点', 60, 'center'),
    ]
    svg_w = sum(c[1] for c in cols) + pad * 2
    content_w = svg_w - pad * 2

    rows_total_h = len(subscription_info) * row_h
    footer_h = 56
    svg_h = title_h + col_header_h + rows_total_h + footer_h + pad * 2

    # 收集数据行
    rows_data = []
    total_nodes = 0
    for info in subscription_info:
        if info.flow:
            f = info.flow
            pct = _pct(f.used, f.total)
            if pct >= 90:
                bar_color = accent_red
            elif pct >= 70:
                bar_color = accent_orange
            else:
                bar_color = accent_green
            if '✅' in f.status:
                status_color = accent_green
            elif '❌' in f.status:
                status_color = accent_red
            else:
                status_color = accent_orange
            rows_data.append({
                'name': _esc(info.name),
                'used': _format_bytes(f.used),
                'total': _format_bytes(f.total),
                'remaining': _format_bytes(f.remaining),
                'pct': pct,
                'bar_color': bar_color,
                'expire': _format_expire(f.expire),
                'status': f.status,
                'status_color': status_color,
                'nodes': str(info.node_count),
            })
        else:
            rows_data.append({
                'name': _esc(info.name), 'used': '—', 'total': '—', 'remaining': '—',
                'pct': 0, 'bar_color': text_secondary, 'expire': '—',
                'status': '❓ 无信息', 'status_color': text_secondary, 'nodes': str(info.node_count),
            })
        total_nodes += info.node_count

    # 构建 SVG
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">',
        # 背景
        f'  <rect width="{svg_w}" height="{svg_h}" rx="{card_radius}" fill="{bg_color}"/>',
        # 渐变和滤镜定义
        '  <defs>',
        '    <linearGradient id="headerGrad" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#f0f4fa"/>',
        '      <stop offset="50%" stop-color="#f8fafc"/>',
        '      <stop offset="100%" stop-color="#eef2f7"/>',
        '    </linearGradient>',
        '    <linearGradient id="accentGrad" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#3b82f6"/>',
        '      <stop offset="100%" stop-color="#10b981"/>',
        '    </linearGradient>',
        '    <linearGradient id="barGreenGrad" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#34d399"/>',
        '      <stop offset="100%" stop-color="#10b981"/>',
        '    </linearGradient>',
        '    <linearGradient id="barOrangeGrad" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#fbbf24"/>',
        '      <stop offset="100%" stop-color="#f59e0b"/>',
        '    </linearGradient>',
        '    <linearGradient id="barRedGrad" x1="0" y1="0" x2="1" y2="0">',
        '      <stop offset="0%" stop-color="#f87171"/>',
        '      <stop offset="100%" stop-color="#ef4444"/>',
        '    </linearGradient>',
        '    <filter id="cardShadow" x="-2%" y="-2%" width="104%" height="104%">',
        '      <feDropShadow dx="0" dy="1" stdDeviation="2" flood-color="#000000" flood-opacity="0.06"/>',
        '    </filter>',
        '  </defs>',
        # 标题栏
        f'  <rect x="{pad}" y="{pad}" width="{content_w}" height="{title_h}" rx="8" fill="url(#headerGrad)" filter="url(#cardShadow)"/>',
        f'  <rect x="{pad}" y="{pad}" width="4" height="{title_h}" rx="2" fill="url(#accentGrad)"/>',
        f'  <line x1="{pad}" y1="{pad + title_h - 1}" x2="{pad + content_w}" y2="{pad + title_h - 1}" stroke="{border_color}" stroke-width="1"/>',
        # 标题文字
        f'  <text x="{pad + 20}" y="{pad + 32}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="18" font-weight="bold" fill="{text_primary}">SubDl</text>',
        f'  <text x="{pad + 80}" y="{pad + 32}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="18" fill="{text_secondary}">订阅状态</text>',
        f'  <text x="{pad + content_w - 20}" y="{pad + 32}" text-anchor="end" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="11" fill="{text_secondary}">{_esc(now)}</text>',
        # 统计摘要
        f'  <text x="{pad + 20}" y="{pad + 56}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="12" fill="{text_secondary}">共 {len(subscription_info)} 个订阅 · {total_nodes} 个节点</text>',
    ]

    # 列标题
    col_y = pad + title_h + 8
    x_offset = pad
    for col_name, col_w, _ in cols:
        if col_name == '订阅':
            tx = x_offset + 16
            anchor = 'start'
        elif col_name == '流量使用':
            tx = x_offset + 16
            anchor = 'start'
        else:
            tx = x_offset + col_w // 2
            anchor = 'middle'
        parts.append(
            f'  <text x="{tx}" y="{col_y + 22}" text-anchor="{anchor}"'
            f' font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"'
            f' font-size="11" font-weight="600" fill="{text_secondary}" letter-spacing="0.5">{col_name}</text>'
        )
        x_offset += col_w
    parts.append(f'  <line x1="{pad}" y1="{col_y + col_header_h}" x2="{pad + content_w}" y2="{col_y + col_header_h}" stroke="{border_color}" stroke-width="1"/>')

    # 数据行
    rows_start_y = col_y + col_header_h
    for i, rd in enumerate(rows_data):
        ry = rows_start_y + i * row_h
        # 交替行背景（极淡蓝灰色）
        if i % 2 == 1:
            parts.append(f'  <rect x="{pad}" y="{ry}" width="{content_w}" height="{row_h}" fill="#f8fafd"/>')
        # 行底部分隔线
        if i > 0:
            parts.append(f'  <line x1="{pad + 16}" y1="{ry}" x2="{pad + content_w - 16}" y2="{ry}" stroke="{border_color}" stroke-width="0.5"/>')

        x_offset = pad
        cy = ry + row_h // 2 + 5

        # 订阅名
        parts.append(f'  <text x="{x_offset + 16}" y="{cy}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="13" font-weight="600" fill="{text_primary}">{rd["name"]}</text>')
        x_offset += cols[0][1]

        # 流量使用 - 文字 + 进度条
        bar_x = x_offset + 16
        bar_y = ry + 12
        bar_w = cols[1][1] - 32
        bar_h = 8
        fill_w = max(bar_w * rd['pct'] / 100, 0)
        parts.append(f'  <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="4" fill="{bar_bg}"/>')
        if fill_w > 0:
            bar_grad = 'barGreenGrad' if rd['bar_color'] == accent_green else ('barOrangeGrad' if rd['bar_color'] == accent_orange else ('barRedGrad' if rd['bar_color'] == accent_red else None))
            if bar_grad:
                parts.append(f'  <rect x="{bar_x}" y="{bar_y}" width="{fill_w}" height="{bar_h}" rx="4" fill="url(#{bar_grad})"/>')
            else:
                parts.append(f'  <rect x="{bar_x}" y="{bar_y}" width="{fill_w}" height="{bar_h}" rx="4" fill="{rd["bar_color"]}"/>')
        flow_text = f'{rd["used"]} / {rd["total"]}  ({rd["pct"]:.0f}%)'
        parts.append(f'  <text x="{bar_x}" y="{bar_y + bar_h + 16}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="11" fill="{text_secondary}">{flow_text}</text>')
        x_offset += cols[1][1]

        # 到期时间
        parts.append(f'  <text x="{x_offset + cols[2][1] // 2}" y="{cy}" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="12" fill="{text_secondary}">{rd["expire"]}</text>')
        x_offset += cols[2][1]

        # 状态
        parts.append(f'  <text x="{x_offset + cols[3][1] // 2}" y="{cy}" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="13" fill="{rd["status_color"]}">{rd["status"]}</text>')
        x_offset += cols[3][1]

        # 节点数
        parts.append(f'  <text x="{x_offset + cols[4][1] // 2}" y="{cy}" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="13" font-weight="600" fill="{accent_blue}">{rd["nodes"]}</text>')

    # 底部合计栏
    footer_y = rows_start_y + rows_total_h + 8
    parts.append(f'  <line x1="{pad}" y1="{footer_y}" x2="{pad + content_w}" y2="{footer_y}" stroke="{border_color}" stroke-width="1"/>')
    parts.append(
        f'  <text x="{pad + content_w // 2}" y="{footer_y + 32}" text-anchor="middle"'
        f' font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"'
        f' font-size="13" fill="{text_secondary}">合计: <tspan font-weight="700" fill="{accent_blue}">{total_nodes}</tspan> 个节点</text>'
    )

    parts.append('</svg>')
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

    logger.info("::group::Download & Convert subscriptions")
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