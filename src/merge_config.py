#!/usr/bin/env python3
"""
Sing-box 配置合并脚本

将 sing-box 订阅节点合并到 sing-box 配置模板中，生成最终可用的配置文件。

功能：
1. 读取配置模板
2. 处理 outbounds 中的 Subscription 占位符
3. 根据 include 正则筛选节点
4. 设置 tls.insecure = true 以支持自签名证书
5. 处理空 outbound 的兼容性问题
"""

import json
import re
import os
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


def expand_subscription_item(item, subscriptions_nodes, include_regex):
    """
    展开 Subscription 占位符为实际节点列表
    
    Args:
        item: Subscription 对象 {"type": "Subscription", "tag": "xxx"} 或 {"type": "Subscription", "tag": ["sub1", "sub2"]}
        subscriptions_nodes: dict，键为订阅名，值为节点列表
        include_regex: include 正则表达式（可能为 None）
    
    Returns:
        展开后的节点标签列表
    """
    if not isinstance(item, dict) or item.get('type') != 'Subscription':
        return [item]
    
    tag_value = item.get('tag', '')
    
    # 确定要插入的订阅列表
    if tag_value == '' or tag_value is None:
        # 空值表示插入所有订阅
        sub_names = list(subscriptions_nodes.keys())
    elif isinstance(tag_value, list):
        # 数组：插入多个订阅
        sub_names = tag_value
    else:
        # 字符串：单个订阅
        sub_names = [tag_value]
    
    result_tags = []
    
    for sub_name in sub_names:
        if sub_name not in subscriptions_nodes:
            log(f"  警告: 未找到订阅 '{sub_name}'，跳过")
            continue
        
        nodes = subscriptions_nodes[sub_name]
        
        # 如果有 include 正则，筛选节点
        if include_regex:
            try:
                pattern = re.compile(include_regex, re.IGNORECASE)
                filtered = [node for node in nodes if pattern.search(node.get('tag', ''))]
                log(f"    订阅 '{sub_name}': {len(nodes)} 个节点，筛选后 {len(filtered)} 个 (匹配 {include_regex})")
                result_tags.extend([node['tag'] for node in filtered])
            except re.error as e:
                log(f"    错误: include 正则无效: {e}")
                result_tags.extend([node['tag'] for node in nodes])
        else:
            result_tags.extend([node['tag'] for node in nodes])
    
    return result_tags


def process_outbounds(outbounds, subscriptions_nodes, include_regex=None):
    """
    处理 outbounds 数组，展开 Subscription 占位符
    
    Args:
        outbounds: outbounds 数组
        subscriptions_nodes: dict，键为订阅名，值为节点列表
        include_regex: 当前 outbound 的 include 正则（如果有）
    
    Returns:
        处理后的 outbounds 数组
    """
    if not isinstance(outbounds, list):
        return outbounds
    
    result = []
    for item in outbounds:
        if isinstance(item, dict) and item.get('type') == 'Subscription':
            # 展开 Subscription，插入节点标签
            expanded = expand_subscription_item(item, subscriptions_nodes, include_regex)
            for tag in expanded:
                result.append(tag)
        else:
            # 保留其他项（字符串或对象）
            result.append(item)
    
    return result


def remove_include_field(obj):
    """递归移除对象中的 include 字段"""
    if isinstance(obj, dict):
        obj.pop('include', None)
        for value in obj.values():
            remove_include_field(value)
    elif isinstance(obj, list):
        for item in obj:
            remove_include_field(item)


def merge_config(template_config, subscriptions_nodes):
    """
    合并配置
    
    Args:
        template_config: 配置模板字典
        subscriptions_nodes: dict，键为订阅名，值为节点列表
    
    Returns:
        合并后的配置字典
    """
    # 深拷贝配置模板
    config = json.loads(json.dumps(template_config))
    
    # 确保 outbounds 是列表
    if 'outbounds' not in config:
        config['outbounds'] = []
    
    # 统计
    total_subscription_count = 0
    
    # 处理所有 outbounds
    for outbound in config['outbounds']:
        if not isinstance(outbound, dict):
            continue
        
        outbounds_list = outbound.get('outbounds', [])
        if not isinstance(outbounds_list, list):
            continue
        
        # 获取当前 outbound 的 include 正则
        include_regex = outbound.get('include')
        
        # 展开 Subscription
        processed = process_outbounds(outbounds_list, subscriptions_nodes, include_regex)
        outbound['outbounds'] = processed
        
        # 统计 Subscription 展开的节点数
        subscription_count = sum(1 for item in outbounds_list 
                                  if isinstance(item, dict) and item.get('type') == 'Subscription')
        total_subscription_count += subscription_count
    
    log(f"处理了 {total_subscription_count} 个 Subscription 占位符")
    
    # 移除所有 include 字段
    remove_include_field(config)
    
    # 收集所有节点
    all_nodes = []
    for nodes in subscriptions_nodes.values():
        all_nodes.extend(nodes)
    
    # 修复 tls.insecure
    fixed = fix_tls_insecure(all_nodes)
    log(f"已设置 {fixed} 个节点的 tls.insecure = true")
    
    # 将所有代理节点添加到 outbounds 末尾
    config['outbounds'].extend(all_nodes)
    log(f"已添加 {len(all_nodes)} 个代理节点到配置")
    
    # 处理空 outbound 的兼容性问题
    compatible_outbound = {'tag': 'COMPATIBLE', 'type': 'direct'}
    has_compatible = any(o.get('tag') == 'COMPATIBLE' for o in config['outbounds'])
    
    for outbound in config['outbounds']:
        if not isinstance(outbound, dict):
            continue
        
        outbounds_list = outbound.get('outbounds', [])
        if not isinstance(outbounds_list, list) or len(outbounds_list) == 0:
            if not has_compatible:
                config['outbounds'].append(compatible_outbound)
                has_compatible = True
            outbound['outbounds'] = outbounds_list
            outbound['outbounds'].append('COMPATIBLE')
            log(f"  {outbound.get('tag')} -> 空 outbound，添加 COMPATIBLE")
    
    return config


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='合并 sing-box 订阅到配置模板')
    parser.add_argument('template', help='配置模板文件路径 (.json 或 .jsonc)')
    parser.add_argument('subscription', help='sing-box 订阅文件路径')
    parser.add_argument('-o', '--output', help='输出文件路径 (默认输出到 stdout)')
    
    args = parser.parse_args()
    
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
    # 订阅文件格式: {"feiniaoyun": [...nodes...], "shanhai": [...nodes...]}
    if isinstance(subscription, dict):
        subscriptions_nodes = subscription
    else:
        # 如果是列表，默认放到 "default" 订阅
        nodes = subscription if isinstance(subscription, list) else subscription.get('outbounds', [])
        subscriptions_nodes = {"default": nodes}
    
    for sub_name, nodes in subscriptions_nodes.items():
        log(f"订阅 '{sub_name}': {len(nodes)} 个节点")
    
    # 合并配置
    merged = merge_config(template, subscriptions_nodes)
    
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
