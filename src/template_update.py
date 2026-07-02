"""Template Update 独立脚本

负责从 config_template/sing-box.jsonc 生成模板变体文件：
- mixed
- tproxy
- tun-win

与 Subscriptions Update 完全解耦，可单独运行。
"""

import copy
import json
from typing import Any

from utils import log_info, log_error, load_jsonc, TEMPLATE_DIR, TEMPLATE_BASE


# ========== 模板变体生成 ==========

_TEMPLATE_VARIANTS: list[tuple[str, str, str]] = [
    ('mixed', 'mixed', 'remove_tun'),
    ('tproxy', 'tproxy', 'replace_tun_with_tproxy'),
    ('tun-win', 'tun-win', 'remove_auto_redirect'),
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
    """生成所有模板变体（mixed / tproxy / tun-win）"""
    base_template = load_jsonc(TEMPLATE_BASE)
    for suffix, label, action in _TEMPLATE_VARIANTS:
        try:
            template = copy.deepcopy(base_template)
            _transform_template(template, action)
            output_path = TEMPLATE_DIR / f'sing-box-{suffix}.jsonc'
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(template, f, indent=2, ensure_ascii=False)
            log_info(f"  ✓ 已生成 {label} 模板")
        except Exception as e:
            log_error(f"  ✗ 生成 {label} 模板异常: {e}")


if __name__ == "__main__":
    log_info("开始生成模板变体...")
    generate_all_template_variants()
    log_info("模板变体生成完成。")
