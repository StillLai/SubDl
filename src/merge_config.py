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

import json
import re
import sys


def log(msg):
    """日志输出到 stderr"""
    print(f"[Merge] {msg}", file=sys.stderr)


def load_jsonc(filepath):
    """加载 JSONC 文件（支持注释）"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 移除 JSONC 注释（但要避免误删 URL 中的 //）
    lines = []
    for line in content.split('\n'):
        # 移除行首的 // 注释（允许前面有空格）
        stripped = line.lstrip()
        if stripped.startswith('//'):
            # 整行都是注释
            indent = line[:len(line) - len(line.lstrip())]
            lines.append(indent)
        else:
            lines.append(line)
    
    content = '\n'.join(lines)
    return json.loads(content)


def fix_tls_insecure(proxies):
    """遍历所有节点，将 tls.insecure 设为 true"""
    fixed_count = 0
    for proxy in proxies:
        if 'tls' in proxy and isinstance(proxy['tls'], dict):
            proxy['tls']['insecure'] = True
            fixed_count += 1
    return fixed_count


def filter_nodes_by_regex(node_tags, include_regex, exclude_regex):
    """
    根据 include/exclude 正则筛选节点标签
    
    Args:
        node_tags: 节点标签列表
        include_regex: include 正则表达式（可能为 None）
        exclude_regex: exclude 正则表达式（可能为 None）
    
    Returns:
        筛选后的节点标签列表
    """
    if not include_regex and not exclude_regex:
        return node_tags
    
    include_pattern = re.compile(include_regex, re.IGNORECASE) if include_regex else None
    exclude_pattern = re.compile(exclude_regex, re.IGNORECASE) if exclude_regex else None
    
    filtered = []
    for tag in node_tags:
        if include_pattern and not include_pattern.search(tag):
            continue
        if exclude_pattern and exclude_pattern.search(tag):
            continue
        filtered.append(tag)
    
    return filtered


def process_providers(config, subscriptions_nodes):
    """
    处理 providers 配置，将 providers 数组展开为节点标签
    
    1. 检测模板是否有 providers 数组
    2. 遍历 providers，将订阅节点展开为标签列表
    3. 遍历 outbounds，找到有 providers 字段的项
    4. 应用 include/exclude 筛选
    5. 将 providers 字段替换为展开的节点标签列表
    6. 从最终配置中移除 providers 字段
    
    Args:
        config: 配置字典
        subscriptions_nodes: dict，键为订阅名，值为节点列表
    
    Returns:
        处理后的配置字典
    """
    if 'providers' not in config or not isinstance(config['providers'], list):
        return config
    
    providers = config['providers']
    provider_nodes = {}  # provider_tag -> [节点标签列表]
    
    # 展开每个 provider 的节点
    for provider in providers:
        tag = provider.get('tag', '')
        if tag in subscriptions_nodes:
            tags = []
            for node in subscriptions_nodes[tag]:
                if isinstance(node, dict) and 'tag' in node:
                    tags.append(node['tag'])
            provider_nodes[tag] = tags
    
    # 遍历 outbounds，展开 providers 引用
    for outbound in config['outbounds']:
        expanded = []  # 每个 outbound 单独初始化
        if not isinstance(outbound, dict):
            continue
        
        # 获取 include/exclude 正则
        include_regex = outbound.get('include')
        exclude_regex = outbound.get('exclude')
        
        # 判断是 use_all_providers 还是 providers 列表
        use_all = outbound.get('use_all_providers', False)
        
        # 获取原有 outbounds（仅字符串元素，用于保留如 "🎯 全球直连" 等固定项）
        existing_outbounds = outbound.get('outbounds', [])
        fixed_outbounds = []
        if isinstance(existing_outbounds, list):
            # 保留非订阅节点（字符串项，如 "🎯 全球直连"）
            fixed_outbounds = [o for o in existing_outbounds if isinstance(o, str)]
        
        if use_all:
            # use_all_providers: true - 展开所有订阅节点
            for provider_tag in provider_nodes.keys():
                filtered = filter_nodes_by_regex(
                    provider_nodes[provider_tag], include_regex, exclude_regex
                )
                expanded.extend(filtered)
            del outbound['use_all_providers']  # 移除 use_all_providers 字段
            if 'providers' in outbound:
                del outbound['providers']  # 也要删除 providers
        elif 'providers' in outbound:
            # providers 列表 - 展开指定的 providers
            for provider_tag in outbound['providers']:
                if provider_tag in provider_nodes:
                    # 应用筛选
                    filtered = filter_nodes_by_regex(
                        provider_nodes[provider_tag], include_regex, exclude_regex
                    )
                    expanded.extend(filtered)
            del outbound['providers']  # 移除 providers 字段
        else:
            continue
        
        # 追加到原有 outbounds（固定项在前，订阅节点在后）
        outbound['outbounds'] = fixed_outbounds + expanded
    
    # 移除顶层 providers
    del config['providers']
    
    return config


def remove_filter_fields(obj):
    """递归移除对象中的 include 和 exclude 字段"""
    if isinstance(obj, dict):
        obj.pop('include', None)
        obj.pop('exclude', None)
        for value in obj.values():
            remove_filter_fields(value)
    elif isinstance(obj, list):
        for item in obj:
            remove_filter_fields(item)


def merge_config(template_config, subscriptions_nodes, tls_insecure=False):
    """
    合并配置
    
    Args:
        template_config: 配置模板字典
        subscriptions_nodes: dict，键为订阅名，值为节点列表
        tls_insecure: 是否设置所有节点的 tls.insecure = true
    
    Returns:
        合并后的配置字典
    """
    # 深拷贝配置模板
    config = json.loads(json.dumps(template_config))
    
    # 确保 outbounds 是列表
    if 'outbounds' not in config:
        config['outbounds'] = []
    
    # ========== 步骤 1: 收集所有节点并添加订阅前缀 ==========
    all_nodes = []
    for sub_name, nodes in subscriptions_nodes.items():
        for node in nodes:
            if isinstance(node, dict) and 'tag' in node:
                node['tag'] = f"{sub_name}/{node['tag']}"
        all_nodes.extend(nodes)
    
    log(f"已收集 {len(all_nodes)} 个节点并添加订阅前缀")
    
    # ========== 步骤 2: 修复 tls.insecure (可选) ==========
    if tls_insecure:
        fixed = fix_tls_insecure(all_nodes)
        log(f"已设置 {fixed} 个节点的 tls.insecure = true")
    
    # ========== 步骤 3: 处理 providers 配置 ==========
    if 'providers' in config:
        process_providers(config, subscriptions_nodes)
        log("已处理 providers 配置")
    
    # ========== 步骤 4: 移除所有 include 和 exclude 字段 ==========
    remove_filter_fields(config)
    
    # ========== 步骤 5: 将代理节点添加到 outbounds 末尾 ==========
    config['outbounds'].extend(all_nodes)
    log(f"已添加 {len(all_nodes)} 个代理节点到配置")
    
    # ========== 步骤 6: 处理空 outbound 的兼容性问题 ==========
    for outbound in config['outbounds']:
        if not isinstance(outbound, dict):
            continue
        
        # 只有 selector 和 urltest 类型需要有 outbounds 列表
        outbound_type = outbound.get('type', '')
        if outbound_type not in ('selector', 'urltest'):
            continue
        
        outbounds_list = outbound.get('outbounds', [])
        if not isinstance(outbounds_list, list) or len(outbounds_list) == 0:
            outbound['outbounds'] = ['Compatible']
            log(f"  {outbound.get('tag')} -> 空 outbound，添加 Compatible")
    
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
        log("已添加 Compatible outbound 定义")

    return config


def main():
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
        log("stdin 模式：从标准输入读取 JSON")
        input_data = json.loads(sys.stdin.read())
        output = json.dumps(input_data, indent=2, ensure_ascii=False)
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(output)
            log(f"已保存到: {args.output}")
        else:
            print(output)
        return
    
    # ---------- 文件模式 ----------
    if args.template is None or args.subscription is None:
        parser.error("需要同时提供 template 和 subscription 位置参数，或不提供任何参数以使用 stdin 模式")
    
    # 加载配置模板
    template_path = args.template
    if template_path.endswith('.jsonc'):
        template = load_jsonc(template_path)
    else:
        with open(template_path, 'r', encoding='utf-8') as f:
            template = json.load(f)
    log(f"已加载配置模板: {template_path}")
    
    # 加载订阅
    sub_path = args.subscription
    with open(sub_path, 'r', encoding='utf-8') as f:
        subscription = json.load(f)
    log(f"已加载订阅: {sub_path}")
    
    # 按订阅名分组节点
    # 订阅文件格式: {"outbounds": [...], "endpoints": [...]} (singbox 标准格式)
    # 或 {"feiniaoyun": [...nodes...], "shanhai": [...nodes...]} (多订阅分组格式)
    if isinstance(subscription, dict):
        # 提取 outbounds 和 endpoints（singbox 新格式）
        all_nodes = subscription.get('outbounds', []) + subscription.get('endpoints', [])
        if all_nodes:
            # 有 outbounds/endpoints 包装，使用 "default" 订阅
            subscriptions_nodes = {"default": all_nodes}
        else:
            # 旧格式：直接是 {"feiniaoyun": [...nodes...]} 的形式
            subscriptions_nodes = subscription
    else:
        # 异常情况：subscription 应该是 dict，如果不是则报错
        raise ValueError(f"订阅文件格式错误：期望 dict，实际为 {type(subscription).__name__}")
    
    for sub_name, nodes in subscriptions_nodes.items():
        log(f"订阅 '{sub_name}': {len(nodes)} 个节点")
    
    # 合并配置
    merged = merge_config(template, subscriptions_nodes, tls_insecure=args.tls_insecure)
    
    # 输出结果
    output = json.dumps(merged, indent=2, ensure_ascii=False)
    
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        log(f"已保存到: {args.output}")
    else:
        print(output)


if __name__ == '__main__':
    main()