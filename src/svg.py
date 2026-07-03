"""订阅状态 SVG 图片生成模块

生成浅色/深色两种主题的 SVG 状态图，展示订阅流量、到期时间、节点数量等信息。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from utils import FlowInfo, SubscriptionInfo


# ========== 常量 ==========

_FONT = '-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif'
_SVG_PAD = 20
_SVG_ROW_H = 56
_SVG_TITLE_H = 70
_SVG_COL_HEADER_H = 36
_SVG_COLS: list[tuple[str, int]] = [
    ('订阅', 100), ('流量使用', 280), ('到期时间', 90), ('状态', 70), ('节点', 60),
]


# ========== 工具函数 ==========

def _fmt_bytes(n: int) -> str:
    """格式化字节数为人类可读形式"""
    if n == 0:
        return "0 B"
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    i = min(int(n.bit_length() - 1) // 10, len(units) - 1)
    return f"{n / (1024 ** i):.2f} {units[i]}"


def _fmt_expire(ts: int | None) -> str:
    """格式化过期时间戳"""
    if not ts:
        return "无"
    try:
        return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    except Exception:
        return "无"


def _svg_esc(s: str) -> str:
    """SVG 特殊字符转义"""
    return s.replace('&', '&').replace('<', '<').replace('>', '>')


# ========== SVG 主题定义 ==========

@dataclass
class SvgTheme:
    """SVG 主题配色"""
    bg: str
    border: str
    text_pri: str
    text_sec: str
    blue: str
    green: str
    orange: str
    red: str
    bar_bg: str
    row_alt: str
    header_grad_start: str
    header_grad_mid: str
    header_grad_end: str
    accent_grad_start: str
    accent_grad_end: str
    bar_grad_green_start: str
    bar_grad_green_end: str
    bar_grad_orange_start: str
    bar_grad_orange_end: str
    bar_grad_red_start: str
    bar_grad_red_end: str
    shadow_opacity: str


# 浅色主题
LIGHT_THEME = SvgTheme(
    bg='#f0f2f5',
    border='#e2e8f0',
    text_pri='#1e293b',
    text_sec='#64748b',
    blue='#3b82f6',
    green='#10b981',
    orange='#f59e0b',
    red='#ef4444',
    bar_bg='#e2e8f0',
    row_alt='#f8fafd',
    header_grad_start='#f0f4fa',
    header_grad_mid='#f8fafc',
    header_grad_end='#eef2f7',
    accent_grad_start='#3b82f6',
    accent_grad_end='#10b981',
    bar_grad_green_start='#34d399',
    bar_grad_green_end='#10b981',
    bar_grad_orange_start='#fbbf24',
    bar_grad_orange_end='#f59e0b',
    bar_grad_red_start='#f87171',
    bar_grad_red_end='#ef4444',
    shadow_opacity='0.06',
)

# 深色主题
DARK_THEME = SvgTheme(
    bg='#1a1b1e',
    border='#2d2e32',
    text_pri='#e2e8f0',
    text_sec='#94a3b8',
    blue='#60a5fa',
    green='#34d399',
    orange='#fbbf24',
    red='#f87171',
    bar_bg='#2d2e32',
    row_alt='#1f2023',
    header_grad_start='#1e2025',
    header_grad_mid='#252629',
    header_grad_end='#1a1c1f',
    accent_grad_start='#60a5fa',
    accent_grad_end='#34d399',
    bar_grad_green_start='#6ee7b7',
    bar_grad_green_end='#34d399',
    bar_grad_orange_start='#fde68a',
    bar_grad_orange_end='#fbbf24',
    bar_grad_red_start='#fca5a5',
    bar_grad_red_end='#f87171',
    shadow_opacity='0.2',
)


# ========== SVG 内部构建函数 ==========

def _flow_status_text(flow: FlowInfo, theme: SvgTheme) -> tuple[str, str]:
    """返回 (状态文本, 颜色)"""
    now = time.time()
    if flow.expire and flow.expire < now:
        return "❌ 已过期", theme.red
    used = flow.upload + flow.download
    if flow.total > 0 and used >= flow.total:
        return "❌ 流量用完", theme.red
    if flow.expire and flow.expire - now < 7 * 24 * 3600:
        return "⚠️ 即将到期", theme.orange
    return "✅ 正常", theme.green


def _bar_color_and_grad(color: str, theme: SvgTheme) -> tuple[str, str | None]:
    """返回 (bar_color_hex, gradient_id)，gradient_id 为 None 时直接用 hex 填充"""
    if color == theme.green:
        return theme.green, 'barGreenGrad'
    if color == theme.orange:
        return theme.orange, 'barOrangeGrad'
    if color == theme.red:
        return theme.red, 'barRedGrad'
    return color, None


def _build_svg_rows_data(
    subscription_info: list[SubscriptionInfo], theme: SvgTheme,
) -> tuple[list[dict[str, Any]], int]:
    """收集 SVG 数据行，返回 (rows_data, total_nodes)"""
    rows_data: list[dict[str, Any]] = []
    total_nodes = 0

    for info in subscription_info:
        name_display = _svg_esc(info.name)
        env_name_display = _svg_esc(info.env_name) if info.env_name else ''
        if info.from_backup:
            name_display += ' 📦'

        if info.flow:
            f = info.flow
            used = f.upload + f.download
            pct = min(used / f.total * 100, 100.0) if f.total > 0 else 0.0
            bar_color = theme.red if pct >= 90 else (theme.orange if pct >= 70 else theme.green)
            status_text, status_color = _flow_status_text(f, theme)
            if info.from_backup:
                status_text, status_color = '📦 备份', theme.orange
            remaining = f.total - used if f.total > 0 else 0
            rows_data.append({
                'env_name': env_name_display, 'name': name_display,
                'used': _fmt_bytes(used), 'total': _fmt_bytes(f.total),
                'remaining': _fmt_bytes(remaining), 'pct': pct,
                'bar_color': bar_color, 'expire': _fmt_expire(f.expire),
                'status': status_text, 'status_color': status_color,
                'nodes': str(info.node_count),
            })
        else:
            status_text = '📦 备份' if info.from_backup else '❓ 无信息'
            status_color = theme.orange if info.from_backup else theme.text_sec
            rows_data.append({
                'env_name': env_name_display, 'name': name_display,
                'used': '—', 'total': '—', 'remaining': '—',
                'pct': 0, 'bar_color': theme.text_sec, 'expire': '—',
                'status': status_text, 'status_color': status_color,
                'nodes': str(info.node_count),
            })
        total_nodes += info.node_count

    return rows_data, total_nodes


def _build_svg_header(
    svg_w: int, svg_h: int, content_w: int,
    sub_count: int, total_nodes: int, now: str, theme: SvgTheme,
) -> list[str]:
    """构建 SVG 头部：根元素 + 背景 + 渐变/滤镜 + 标题栏"""
    p = _SVG_PAD
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">',
        f'  <rect width="{svg_w}" height="{svg_h}" rx="12" fill="{theme.bg}"/>',
        '  <defs>',
        f'    <linearGradient id="headerGrad" x1="0" y1="0" x2="1" y2="0">',
        f'      <stop offset="0%" stop-color="{theme.header_grad_start}"/>',
        f'      <stop offset="50%" stop-color="{theme.header_grad_mid}"/>',
        f'      <stop offset="100%" stop-color="{theme.header_grad_end}"/>',
        '    </linearGradient>',
        f'    <linearGradient id="accentGrad" x1="0" y1="0" x2="1" y2="0">',
        f'      <stop offset="0%" stop-color="{theme.accent_grad_start}"/>',
        f'      <stop offset="100%" stop-color="{theme.accent_grad_end}"/>',
        '    </linearGradient>',
        f'    <linearGradient id="barGreenGrad" x1="0" y1="0" x2="1" y2="0">',
        f'      <stop offset="0%" stop-color="{theme.bar_grad_green_start}"/>',
        f'      <stop offset="100%" stop-color="{theme.bar_grad_green_end}"/>',
        '    </linearGradient>',
        f'    <linearGradient id="barOrangeGrad" x1="0" y1="0" x2="1" y2="0">',
        f'      <stop offset="0%" stop-color="{theme.bar_grad_orange_start}"/>',
        f'      <stop offset="100%" stop-color="{theme.bar_grad_orange_end}"/>',
        '    </linearGradient>',
        f'    <linearGradient id="barRedGrad" x1="0" y1="0" x2="1" y2="0">',
        f'      <stop offset="0%" stop-color="{theme.bar_grad_red_start}"/>',
        f'      <stop offset="100%" stop-color="{theme.bar_grad_red_end}"/>',
        '    </linearGradient>',
        f'    <filter id="cardShadow" x="-2%" y="-2%" width="104%" height="104%">',
        f'      <feDropShadow dx="0" dy="1" stdDeviation="2" flood-color="#000000" flood-opacity="{theme.shadow_opacity}"/>',
        '    </filter>',
        '  </defs>',
        f'  <rect x="{p}" y="{p}" width="{content_w}" height="{_SVG_TITLE_H}" rx="8" fill="url(#headerGrad)" filter="url(#cardShadow)"/>',
        f'  <rect x="{p}" y="{p}" width="4" height="{_SVG_TITLE_H}" rx="2" fill="url(#accentGrad)"/>',
        f'  <line x1="{p}" y1="{p + _SVG_TITLE_H - 1}" x2="{p + content_w}" y2="{p + _SVG_TITLE_H - 1}" stroke="{theme.border}" stroke-width="1"/>',
        f'  <text x="{p + 20}" y="{p + 32}" font-family="{_FONT}" font-size="18" font-weight="bold" fill="{theme.text_pri}">SubDl</text>',
        f'  <text x="{p + 80}" y="{p + 32}" font-family="{_FONT}" font-size="18" fill="{theme.text_sec}">订阅状态</text>',
        f'  <text x="{p + content_w - 20}" y="{p + 32}" text-anchor="end" font-family="{_FONT}" font-size="11" fill="{theme.text_sec}">{_svg_esc(now)}</text>',
        f'  <text x="{p + 20}" y="{p + 56}" font-family="{_FONT}" font-size="12" fill="{theme.text_sec}">共 {sub_count} 个订阅 · {total_nodes} 个节点</text>',
    ]


def _build_svg_table(
    rows_data: list[dict[str, Any]], content_w: int, table_y: int, theme: SvgTheme,
) -> list[str]:
    """构建 SVG 表格：列标题 + 数据行"""
    p = _SVG_PAD
    parts: list[str] = []

    # 列标题
    x_offset = p
    for col_name, col_w in _SVG_COLS:
        tx = x_offset + 16 if col_name in ('订阅', '流量使用') else x_offset + col_w // 2
        anchor = 'start' if col_name in ('订阅', '流量使用') else 'middle'
        parts.append(
            f'  <text x="{tx}" y="{table_y + 22}" text-anchor="{anchor}"'
            f' font-family="{_FONT}"'
            f' font-size="11" font-weight="600" fill="{theme.text_sec}" letter-spacing="0.5">{col_name}</text>'
        )
        x_offset += col_w
    parts.append(f'  <line x1="{p}" y1="{table_y + _SVG_COL_HEADER_H}" x2="{p + content_w}" y2="{table_y + _SVG_COL_HEADER_H}" stroke="{theme.border}" stroke-width="1"/>')

    # 数据行
    rows_start_y = table_y + _SVG_COL_HEADER_H
    for i, rd in enumerate(rows_data):
        ry = rows_start_y + i * _SVG_ROW_H
        if i % 2 == 1:
            parts.append(f'  <rect x="{p}" y="{ry}" width="{content_w}" height="{_SVG_ROW_H}" fill="{theme.row_alt}"/>')
        if i > 0:
            parts.append(f'  <line x1="{p + 16}" y1="{ry}" x2="{p + content_w - 16}" y2="{ry}" stroke="{theme.border}" stroke-width="0.5"/>')

        x_offset = p
        cy = ry + _SVG_ROW_H // 2 + 5

        # 环境变量名 + 订阅名
        if rd.get("env_name"):
            parts.append(f'  <text x="{x_offset + 16}" y="{ry + 18}" font-family="{_FONT}" font-size="10" fill="{theme.text_sec}">{rd["env_name"]}</text>')
            parts.append(f'  <text x="{x_offset + 16}" y="{ry + 38}" font-family="{_FONT}" font-size="13" font-weight="600" fill="{theme.text_pri}">{rd["name"]}</text>')
        else:
            parts.append(f'  <text x="{x_offset + 16}" y="{cy}" font-family="{_FONT}" font-size="13" font-weight="600" fill="{theme.text_pri}">{rd["name"]}</text>')
        x_offset += _SVG_COLS[0][1]

        # 流量使用 - 进度条 + 文字
        bar_x = x_offset + 16
        bar_y = ry + 12
        bar_w = _SVG_COLS[1][1] - 32
        fill_w = max(bar_w * rd['pct'] / 100, 0)
        parts.append(f'  <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="8" rx="4" fill="{theme.bar_bg}"/>')
        if fill_w > 0:
            _, bar_grad = _bar_color_and_grad(rd['bar_color'], theme)
            fill_val = f"url(#{bar_grad})" if bar_grad else rd['bar_color']
            parts.append(f'  <rect x="{bar_x}" y="{bar_y}" width="{fill_w}" height="8" rx="4" fill="{fill_val}"/>')
        parts.append(f'  <text x="{bar_x}" y="{bar_y + 24}" font-family="{_FONT}" font-size="11" fill="{theme.text_sec}">{rd["used"]} / {rd["total"]}  ({rd["pct"]:.0f}%)  剩余: {rd["remaining"]}</text>')
        x_offset += _SVG_COLS[1][1]

        # 到期时间
        parts.append(f'  <text x="{x_offset + _SVG_COLS[2][1] // 2}" y="{cy}" text-anchor="middle" font-family="{_FONT}" font-size="12" fill="{theme.text_sec}">{rd["expire"]}</text>')
        x_offset += _SVG_COLS[2][1]

        # 状态
        parts.append(f'  <text x="{x_offset + _SVG_COLS[3][1] // 2}" y="{cy}" text-anchor="middle" font-family="{_FONT}" font-size="13" fill="{rd["status_color"]}">{rd["status"]}</text>')
        x_offset += _SVG_COLS[3][1]

        # 节点数
        parts.append(f'  <text x="{x_offset + _SVG_COLS[4][1] // 2}" y="{cy}" text-anchor="middle" font-family="{_FONT}" font-size="13" font-weight="600" fill="{theme.blue}">{rd["nodes"]}</text>')

    return parts


def _build_svg_footer(
    total_nodes: int, versions: dict[str, str] | None,
    content_w: int, footer_y: int, theme: SvgTheme,
) -> list[str]:
    """构建 SVG 底部：合计栏 + 版本信息 + 关闭标签"""
    p = _SVG_PAD
    parts: list[str] = [
        f'  <line x1="{p}" y1="{footer_y}" x2="{p + content_w}" y2="{footer_y}" stroke="{theme.border}" stroke-width="1"/>',
        f'  <text x="{p + content_w // 2}" y="{footer_y + 32}" text-anchor="middle"'
        f' font-family="{_FONT}"'
        f' font-size="13" fill="{theme.text_sec}">合计: <tspan font-weight="700" fill="{theme.blue}">{total_nodes}</tspan> 个节点</text>',
    ]

    if versions:
        ver_y = footer_y + 56
        parts.append(f'  <line x1="{p}" y1="{ver_y}" x2="{p + content_w}" y2="{ver_y}" stroke="{theme.border}" stroke-width="1"/>')
        official_ver = _svg_esc(versions.get('official', '—'))
        ref1nd_ver = _svg_esc(versions.get('reF1nd', '—'))
        parts.append(
            f'  <text x="{p + 20}" y="{ver_y + 22}" font-family="{_FONT}" font-size="12" font-weight="600" fill="{theme.text_sec}">sing-box 版本</text>'
        )
        parts.append(
            f'  <text x="{p + 20}" y="{ver_y + 40}" font-family="{_FONT}" font-size="11" fill="{theme.text_sec}">'
            f'官方版: <tspan font-weight="600" fill="{theme.text_pri}">{official_ver}</tspan>'
            f'　|　reF1nd 分支: <tspan font-weight="600" fill="{theme.text_pri}">{ref1nd_ver}</tspan>'
            f'</text>'
        )

    parts.append('</svg>')
    return parts


# ========== SVG 主函数 ==========

def generate_status_svg(
    subscription_info: list[SubscriptionInfo], versions: dict[str, str] | None = None,
    theme: SvgTheme | None = None,
) -> str:
    """生成订阅状态 SVG 图片（支持浅色/深色主题）"""
    if theme is None:
        theme = LIGHT_THEME
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S CST')

    svg_w = sum(c[1] for c in _SVG_COLS) + _SVG_PAD * 2
    content_w = svg_w - _SVG_PAD * 2
    rows_data, total_nodes = _build_svg_rows_data(subscription_info, theme)
    rows_total_h = len(subscription_info) * _SVG_ROW_H
    version_h = 52 if versions else 0
    svg_h = _SVG_TITLE_H + _SVG_COL_HEADER_H + rows_total_h + 56 + version_h + _SVG_PAD * 2

    table_y = _SVG_PAD + _SVG_TITLE_H + 8
    footer_y = table_y + _SVG_COL_HEADER_H + rows_total_h + 8

    parts = (
        _build_svg_header(svg_w, svg_h, content_w, len(subscription_info), total_nodes, now, theme)
        + _build_svg_table(rows_data, content_w, table_y, theme)
        + _build_svg_footer(total_nodes, versions, content_w, footer_y, theme)
    )
    return '\n'.join(parts)