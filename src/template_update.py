"""Template Update 独立脚本

负责从 config_template/sing-box_template.jsonc 生成模板变体文件：
- noTun
- tproxy
- tun_for_win

与 Subscriptions Update 完全解耦，可单独运行。
"""

from __future__ import annotations

import copy
import json
from typing import Any

from utils import logger, load_jsonc, TEMPLATE_DIR, TEMPLATE_BASE


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


def main() -> None:
    logger.info("开始生成模板变体...")
    generate_all_template_variants()
    logger.info("模板变体生成完成。")


if __name__ == "__main__":
    main()