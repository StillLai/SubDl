"""
Sing-box 配置合并模块

将 sing-box 订阅节点合并到 sing-box 配置模板中，生成最终可用的配置文件。

功能：
1. 读取配置模板
2. 处理 providers 配置（将 providers 数组展开为节点标签）
3. 根据 include/exclude 正则筛选节点
4. 处理空 outbound 的兼容性问题
"""

import copy
import re
from typing import Any

from utils import log_info, log_warn


def filter_nodes_by_regex(
    node_tags: list[str], include_regex: str | None, exclude_regex: str | None
) -> list[str]:
    """根据 include/exclude 正则筛选节点标签"""
    if not include_regex and not exclude_regex:
        return node_tags

    include_pattern = re.compile(include_regex, re.IGNORECASE) if include_regex else None
    exclude_pattern = re.compile(exclude_regex, re.IGNORECASE) if exclude_regex else None

    return [
        tag for tag in node_tags
        if (not include_pattern or include_pattern.search(tag))
        and (not exclude_pattern or not exclude_pattern.search(tag))
    ]


def process_providers(
    config: dict[str, Any], subscriptions_nodes: dict[str, list[dict[str, Any]]]
) -> None:
    """
    处理 providers 配置，将 providers 数组展开为节点标签（原地修改）

    前缀一致性约束：
       subscriptions_nodes 的 key 是 sub_name（即 provider 的 tag），
       merge_config 步骤 1 中会给所有节点 tag 加上 "{sub_name}/" 前缀
       再追加到 outbounds。因此本函数展开 provider 引用时，也必须使用
       相同的 "{provider_tag}/{node_tag}" 格式，否则 selector 中的引用
       会指向不存在的节点 tag，导致路由失效。
    """
    providers = config.get('providers')
    if not isinstance(providers, list):
        return

    # 展开每个 provider 的节点
    provider_nodes: dict[str, list[str]] = {}
    for provider in providers:
        tag = provider.get('tag', '')
        if tag in subscriptions_nodes:
            provider_nodes[tag] = [
                f"{tag}/{node['tag']}" for node in subscriptions_nodes[tag]
                if isinstance(node, dict) and 'tag' in node
            ]

    # 遍历 outbounds，展开 providers 引用
    for outbound in config['outbounds']:
        if not isinstance(outbound, dict):
            continue

        use_all = outbound.get('use_all_providers', False)
        existing_outbounds = outbound.get('outbounds', [])
        fixed_outbounds = [o for o in existing_outbounds if isinstance(o, str)]

        provider_tags = list(provider_nodes) if use_all else outbound.get('providers', [])
        outbound.pop('use_all_providers', None)
        outbound.pop('providers', None)

        # 检测可能的拼写错误：如果 outbound 有看起来像 providers 配置的字段但没有被处理
        tag = outbound.get('tag', '<unknown>')
        if not provider_tags and not fixed_outbounds and outbound.get('type') in ('selector', 'urltest'):
            log_warn(f"  {tag}: selector/urltest 类型没有 providers 也没有 outbounds，可能配置有误")

        if not provider_tags:
            continue

        include_regex = outbound.get('include')
        exclude_regex = outbound.get('exclude')

        expanded: list[str] = []
        for provider_tag in provider_tags:
            if provider_tag in provider_nodes:
                expanded.extend(filter_nodes_by_regex(
                    provider_nodes[provider_tag], include_regex, exclude_regex
                ))
        outbound['outbounds'] = fixed_outbounds + expanded


def _strip_filter_fields(outbounds: list[Any]) -> None:
    """移除 outbounds 中每个 dict 的 include/exclude 字段（O(n) 常数时间）"""
    for outbound in outbounds:
        if isinstance(outbound, dict):
            outbound.pop('include', None)
            outbound.pop('exclude', None)


def _fix_empty_outbounds(outbounds: list[Any]) -> None:
    """修复 selector/urltest 类型中空 outbounds 的兼容性问题，
    并确保 Compatible outbound 定义存在"""
    has_compatible = False

    for outbound in outbounds:
        if not isinstance(outbound, dict):
            continue
        if outbound.get('tag') == 'Compatible':
            has_compatible = True
        if outbound.get('type') in ('selector', 'urltest'):
            outbounds_list = outbound.get('outbounds', [])
            if not isinstance(outbounds_list, list) or not outbounds_list:
                outbound['outbounds'] = ['Compatible']
                log_info(f"  {outbound.get('tag')} -> 空 outbound，添加 Compatible")

    if not has_compatible:
        outbounds.append({"tag": "Compatible", "type": "direct"})
        log_info("[Merge] 已添加 Compatible outbound 定义")


def merge_config(
    template_config: dict[str, Any],
    subscriptions_nodes: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """合并配置"""
    config = copy.deepcopy(template_config)
    outbounds = config.setdefault('outbounds', [])

    # 步骤 1: 收集所有节点并添加订阅前缀
    all_nodes: list[dict[str, Any]] = []
    for sub_name, nodes in subscriptions_nodes.items():
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_copy = copy.deepcopy(node)
            node_copy['tag'] = f"{sub_name}/{node_copy['tag']}" if 'tag' in node_copy else ''
            all_nodes.append(node_copy)
    log_info(f"[Merge] 已收集 {len(all_nodes)} 个节点并添加订阅前缀")

    # 步骤 2: 处理 providers 配置
    if 'providers' in config:
        process_providers(config, subscriptions_nodes)
        del config['providers']
        log_info("[Merge] 已处理 providers 配置")

    # 步骤 3: 移除 outbounds 中的 include/exclude 字段
    _strip_filter_fields(outbounds)

    # 步骤 4: 将代理节点添加到 outbounds 末尾
    outbounds.extend(all_nodes)
    log_info(f"[Merge] 已添加 {len(all_nodes)} 个代理节点到配置")

    # 步骤 5: 修复空 outbound 兼容性 + 确保 Compatible 定义存在
    _fix_empty_outbounds(outbounds)

    return config