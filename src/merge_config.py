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

from utils import log


def filter_nodes_by_regex(
    node_tags: list[str], include_regex: str | None, exclude_regex: str | None
) -> list[str]:
    """根据 include/exclude 正则筛选节点标签"""
    if not include_regex and not exclude_regex:
        return node_tags

    include_pattern = re.compile(include_regex, re.IGNORECASE) if include_regex else None
    exclude_pattern = re.compile(exclude_regex, re.IGNORECASE) if exclude_regex else None

    filtered: list[str] = []
    for tag in node_tags:
        if include_pattern and not include_pattern.search(tag):
            continue
        if exclude_pattern and exclude_pattern.search(tag):
            continue
        filtered.append(tag)

    return filtered


def process_providers(
    config: dict[str, Any], subscriptions_nodes: dict[str, list[dict[str, Any]]]
) -> None:
    """
    处理 providers 配置，将 providers 数组展开为节点标签（原地修改）

    1. 检测模板是否有 providers 数组
    2. 遍历 providers，将订阅节点展开为标签列表
    3. 遍历 outbounds，找到有 providers 字段的项
    4. 应用 include/exclude 筛选
    5. 将 providers 字段替换为展开的节点标签列表
    6. 从最终配置中移除 providers 字段
    """
    if 'providers' not in config or not isinstance(config['providers'], list):
        return

    providers: list[dict[str, Any]] = config['providers']
    provider_nodes: dict[str, list[str]] = {}  # provider_tag -> [节点标签列表]

    # 展开每个 provider 的节点
    for provider in providers:
        tag: str = provider.get('tag', '')
        if tag in subscriptions_nodes:
            provider_nodes[tag] = [
                node['tag'] for node in subscriptions_nodes[tag]
                if isinstance(node, dict) and 'tag' in node
            ]

    # 遍历 outbounds，展开 providers 引用
    for outbound in config['outbounds']:
        if not isinstance(outbound, dict):
            continue

        include_regex: str | None = outbound.get('include')
        exclude_regex: str | None = outbound.get('exclude')
        use_all: bool = outbound.pop('use_all_providers', False)

        existing_outbounds: list[Any] = outbound.get('outbounds', [])
        fixed_outbounds = [o for o in existing_outbounds if isinstance(o, str)]

        provider_tags = list(provider_nodes) if use_all else outbound.pop('providers', [])
        if not provider_tags and not use_all:
            continue

        expanded: list[str] = []
        for provider_tag in provider_tags:
            if provider_tag in provider_nodes:
                expanded.extend(filter_nodes_by_regex(
                    provider_nodes[provider_tag], include_regex, exclude_regex
                ))
        outbound['outbounds'] = fixed_outbounds + expanded

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


def merge_config(
    template_config: dict[str, Any],
    subscriptions_nodes: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """合并配置"""
    # 深拷贝配置模板
    config: dict[str, Any] = copy.deepcopy(template_config)

    if 'outbounds' not in config:
        config['outbounds'] = []

    # ========== 步骤 1: 收集所有节点并添加订阅前缀 ==========
    # NOTE: 深拷贝节点以避免修改 subscriptions_nodes 中的原始数据
    all_nodes: list[dict[str, Any]] = []
    for sub_name, nodes in subscriptions_nodes.items():
        for node in nodes:
            if isinstance(node, dict) and 'tag' in node:
                node_copy = copy.deepcopy(node)
                node_copy['tag'] = f"{sub_name}/{node_copy['tag']}"
                all_nodes.append(node_copy)
            else:
                all_nodes.append(copy.deepcopy(node))

    log(f"[Merge] 已收集 {len(all_nodes)} 个节点并添加订阅前缀")

    # ========== 步骤 2: 处理 providers 配置 ==========
    if 'providers' in config:
        process_providers(config, subscriptions_nodes)
        log("[Merge] 已处理 providers 配置")

    # ========== 步骤 3: 移除所有 include 和 exclude 字段 ==========
    remove_filter_fields(config.get('outbounds', []))

    # ========== 步骤 4: 将代理节点添加到 outbounds 末尾 ==========
    config['outbounds'].extend(all_nodes)
    log(f"[Merge] 已添加 {len(all_nodes)} 个代理节点到配置")

    # ========== 步骤 5: 处理空 outbound 的兼容性问题 ==========
    for outbound in config['outbounds']:
        if not isinstance(outbound, dict):
            continue

        outbound_type: str = outbound.get('type', '')
        if outbound_type not in ('selector', 'urltest'):
            continue

        outbounds_list: list[Any] = outbound.get('outbounds', [])
        if not isinstance(outbounds_list, list) or len(outbounds_list) == 0:
            outbound['outbounds'] = ['Compatible']
            log(f"[Merge]   {outbound.get('tag')} -> 空 outbound，添加 Compatible")

    # ========== 步骤 6: 添加 Compatible outbound 定义 ==========
    has_compatible = any(
        isinstance(o, dict) and o.get('tag') == 'Compatible'
        for o in config['outbounds']
    )
    if not has_compatible:
        config['outbounds'].append({
            "tag": "Compatible",
            "type": "direct"
        })
        log("[Merge] 已添加 Compatible outbound 定义")

    return config
