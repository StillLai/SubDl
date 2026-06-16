#!/usr/bin/env python3
"""
Sing-box 配置合并脚本

将 sing-box 订阅节点合并到 sing-box 配置模板中，生成最终可用的配置文件。

功能：
1. 读取配置模板
2. 处理 providers 配置（将 providers 数组展开为节点标签）
3. 根据 include/exclude 正则筛选节点
4. 设置 tls.insecure = true 以支持自签名证书
5. 处理空 outbound 的兼容性问题
"""

from __future__ import annotations

import copy
import json
import re
import sys
from typing import Any, TypedDict

from utils import load_jsonc, log


class SingBoxNode(TypedDict, total=False):
    """sing-box 节点的最小公共字段"""
    tag: str
    type: str
    server: str
    server_port: int
    tls: dict[str, Any]
    outbounds: list[str] | list[dict[str, Any]]
    providers: list[str]


NodeList = list[SingBoxNode]
SubsNodesDict = dict[str, NodeList]


def fix_tls_insecure(proxies: NodeList) -> int:
    """遍历所有节点，将 tls.insecure 设为 true"""
    fixed_count = 0
    for proxy in proxies:
        if 'tls' in proxy and isinstance(proxy['tls'], dict):
            proxy['tls']['insecure'] = True
            fixed_count += 1
    return fixed_count


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
) -> dict[str, Any]:
    """
    处理 providers 配置，将 providers 数组展开为节点标签

    1. 检测模板是否有 providers 数组
    2. 遍历 providers，将订阅节点展开为标签列表
    3. 遍历 outbounds，找到有 providers 字段的项
    4. 应用 include/exclude 筛选
    5. 将 providers 字段替换为展开的节点标签列表
    6. 从最终配置中移除 providers 字段
    """
    if 'providers' not in config or not isinstance(config['providers'], list):
        return config

    providers: list[dict[str, Any]] = config['providers']
    provider_nodes: dict[str, list[str]] = {}  # provider_tag -> [节点标签列表]

    # 展开每个 provider 的节点
    for provider in providers:
        tag: str = provider.get('tag', '')
        if tag in subscriptions_nodes:
            tags: list[str] = []
            for node in subscriptions_nodes[tag]:
                if isinstance(node, dict) and 'tag' in node:
                    tags.append(node['tag'])
            provider_nodes[tag] = tags

    # 遍历 outbounds，展开 providers 引用
    for outbound in config['outbounds']:
        expanded: list[str] = []
        if not isinstance(outbound, dict):
            continue

        include_regex: str | None = outbound.get('include')
        exclude_regex: str | None = outbound.get('exclude')
        use_all: bool = outbound.get('use_all_providers', False)

        existing_outbounds: list[Any] = outbound.get('outbounds', [])
        fixed_outbounds: list[str] = []
        if isinstance(existing_outbounds, list):
            fixed_outbounds = [o for o in existing_outbounds if isinstance(o, str)]

        if use_all:
            for provider_tag in provider_nodes.keys():
                filtered = filter_nodes_by_regex(
                    provider_nodes[provider_tag], include_regex, exclude_regex
                )
                expanded.extend(filtered)
            del outbound['use_all_providers']
            if 'providers' in outbound:
                del outbound['providers']
        elif 'providers' in outbound:
            for provider_tag in outbound['providers']:
                if provider_tag in provider_nodes:
                    filtered = filter_nodes_by_regex(
                        provider_nodes[provider_tag], include_regex, exclude_regex
                    )
                    expanded.extend(filtered)
            del outbound['providers']
        else:
            continue

        outbound['outbounds'] = fixed_outbounds + expanded

    del config['providers']
    return config


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
    tls_insecure: bool = False,
) -> dict[str, Any]:
    """合并配置"""
    # 深拷贝配置模板
    config: dict[str, Any] = copy.deepcopy(template_config)

    if 'outbounds' not in config:
        config['outbounds'] = []

    # ========== 步骤 1: 收集所有节点并添加订阅前缀 ==========
    all_nodes: list[dict[str, Any]] = []
    for sub_name, nodes in subscriptions_nodes.items():
        for node in nodes:
            if isinstance(node, dict) and 'tag' in node:
                node['tag'] = f"{sub_name}/{node['tag']}"
        all_nodes.extend(nodes)

    log(f"[Merge] 已收集 {len(all_nodes)} 个节点并添加订阅前缀")

    # ========== 步骤 2: 修复 tls.insecure (可选) ==========
    if tls_insecure:
        fixed = fix_tls_insecure(all_nodes)
        log(f"[Merge] 已设置 {fixed} 个节点的 tls.insecure = true")

    # ========== 步骤 3: 处理 providers 配置 ==========
    if 'providers' in config:
        process_providers(config, subscriptions_nodes)
        log("[Merge] 已处理 providers 配置")

    # ========== 步骤 4: 移除所有 include 和 exclude 字段 ==========
    remove_filter_fields(config)

    # ========== 步骤 5: 将代理节点添加到 outbounds 末尾 ==========
    config['outbounds'].extend(all_nodes)
    log(f"[Merge] 已添加 {len(all_nodes)} 个代理节点到配置")

    # ========== 步骤 6: 处理空 outbound 的兼容性问题 ==========
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

    # ========== 步骤 7: 添加 Compatible outbound 定义 ==========
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


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description='合并 sing-box 订阅到配置模板')
    parser.add_argument('template', nargs='?', help='配置模板文件路径 (.json 或 .jsonc)')
    parser.add_argument('subscription', nargs='?', help='sing-box 订阅文件路径')
    parser.add_argument('-o', '--output', help='输出文件路径 (默认输出到 stdout)')
    parser.add_argument('--tls-insecure', action='store_true', default=False,
                        help='设置所有节点的 tls.insecure = true')

    args = parser.parse_args()

    # ---------- stdin 模式 ----------
    if args.template is None and args.subscription is None:
        log("[Merge] stdin 模式：从标准输入读取 JSON")
        input_data = json.loads(sys.stdin.read())
        output = json.dumps(input_data, indent=2, ensure_ascii=False)
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(output)
            log(f"[Merge] 已保存到: {args.output}")
        else:
            print(output)
        return

    # ---------- 文件模式 ----------
    if args.template is None or args.subscription is None:
        parser.error("需要同时提供 template 和 subscription 位置参数，或不提供任何参数以使用 stdin 模式")

    # 加载配置模板
    template_path: str = args.template
    if template_path.endswith('.jsonc'):
        template = load_jsonc(template_path)
    else:
        with open(template_path, 'r', encoding='utf-8') as f:
            template = json.load(f)
    log(f"[Merge] 已加载配置模板: {template_path}")

    # 加载订阅
    sub_path: str = args.subscription
    with open(sub_path, 'r', encoding='utf-8') as f:
        subscription = json.load(f)
    log(f"[Merge] 已加载订阅: {sub_path}")

    # 按订阅名分组节点
    subscriptions_nodes: dict[str, list[dict[str, Any]]]
    if isinstance(subscription, dict):
        all_nodes = subscription.get('outbounds', []) + subscription.get('endpoints', [])
        if all_nodes:
            subscriptions_nodes = {"default": all_nodes}
        else:
            subscriptions_nodes = subscription
    else:
        raise ValueError(f"订阅文件格式错误：期望 dict，实际为 {type(subscription).__name__}")

    for sub_name, nodes in subscriptions_nodes.items():
        log(f"[Merge] 订阅 '{sub_name}': {len(nodes)} 个节点")

    # 合并配置
    merged = merge_config(template, subscriptions_nodes, tls_insecure=args.tls_insecure)

    # 输出结果
    output = json.dumps(merged, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        log(f"[Merge] 已保存到: {args.output}")
    else:
        print(output)


if __name__ == '__main__':
    main()