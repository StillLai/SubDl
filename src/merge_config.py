"""
Sing-box 配置合并模块

将 sing-box 订阅节点合并到 sing-box 配置模板中，生成最终可用的配置文件。

功能：
1. 读取配置模板
2. 处理 providers 配置（将 providers 数组展开为节点标签）
3. 根据 include/exclude 正则筛选节点
4. 处理空 outbound 的兼容性问题
"""

from __future__ import annotations

import copy
import re
from typing import Any

from utils import logger


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

        include_regex = outbound.get('include')
        exclude_regex = outbound.get('exclude')
        use_all = outbound.get('use_all_providers', False)

        existing_outbounds = outbound.get('outbounds', [])
        fixed_outbounds = [o for o in existing_outbounds if isinstance(o, str)]

        provider_tags = list(provider_nodes) if use_all else outbound.get('providers', [])
        if not provider_tags and not use_all:
            continue
        # 移除已处理的过滤字段
        outbound.pop('use_all_providers', None)
        outbound.pop('providers', None)
        if not provider_tags:
            continue

        expanded: list[str] = []
        for provider_tag in provider_tags:
            if provider_tag in provider_nodes:
                expanded.extend(filter_nodes_by_regex(
                    provider_nodes[provider_tag], include_regex, exclude_regex
                ))
        outbound['outbounds'] = fixed_outbounds + expanded

    # 移除 providers 字段：本函数走"节点全部展开"路径
    del config['providers']


def remove_filter_fields(obj: Any) -> None:
    """递归移除对象中的 include 和 exclude 字段"""
    if isinstance(obj, dict):
        obj.pop('include', None)
        obj.pop('exclude', None)
        for value in obj.values():
            remove_filter_fields(value)
    elif isinstance(obj, list):
        for item in obj:
            remove_filter_fields(item)


def _ensure_compatible_outbound(outbounds: list[Any]) -> None:
    """确保 outbounds 中存在 Compatible outbound 定义"""
    has_compatible = any(
        isinstance(o, dict) and o.get('tag') == 'Compatible'
        for o in outbounds
    )
    if not has_compatible:
        outbounds.append({"tag": "Compatible", "type": "direct"})
        logger.info("[Merge] 已添加 Compatible outbound 定义")


def _fix_empty_selector_outbounds(outbounds: list[Any]) -> None:
    """修复 selector/urltest 类型中空 outbounds 的兼容性问题"""
    for outbound in outbounds:
        if not isinstance(outbound, dict):
            continue
        outbound_type = outbound.get('type', '')
        if outbound_type not in ('selector', 'urltest'):
            continue
        outbounds_list = outbound.get('outbounds', [])
        if not isinstance(outbounds_list, list) or len(outbounds_list) == 0:
            outbound['outbounds'] = ['Compatible']
            logger.debug(f"  {outbound.get('tag')} -> 空 outbound，添加 Compatible")


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
            node_copy['tag'] = f"{sub_name}/{node_copy['tag']}" if 'tag' in node_copy else node_copy.get('tag', '')
            all_nodes.append(node_copy)
    logger.info(f"[Merge] 已收集 {len(all_nodes)} 个节点并添加订阅前缀")

    # 步骤 2: 处理 providers 配置
    if 'providers' in config:
        process_providers(config, subscriptions_nodes)
        logger.info("[Merge] 已处理 providers 配置")

    # 步骤 3: 移除所有 include 和 exclude 字段
    remove_filter_fields(outbounds)

    # 步骤 4: 将代理节点添加到 outbounds 末尾
    outbounds.extend(all_nodes)
    logger.info(f"[Merge] 已添加 {len(all_nodes)} 个代理节点到配置")

    # 步骤 5: 处理空 outbound 的兼容性问题
    _fix_empty_selector_outbounds(outbounds)

    # 步骤 6: 添加 Compatible outbound 定义
    _ensure_compatible_outbound(outbounds)

    return config