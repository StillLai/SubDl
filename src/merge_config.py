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


def build_duplicate_tag_info(all_nodes):
    """
    遍历节点，统计重复 tag 并返回需要重命名的节点信息
    
    Args:
        all_nodes: 所有节点的列表
    
    Returns:
        dict: {tag: [新tag列表]} - 每个重复 tag 的所有新 tag 版本
    """
    # 统计每个 tag 出现次数
    tag_counts = {}
    for node in all_nodes:
        if isinstance(node, dict) and 'tag' in node:
            tag = node['tag']
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    
    # 为每个重复 tag 生成新 tag 列表
    duplicate_tag_info = {}
    for tag, count in tag_counts.items():
        if count > 1:
            # 生成所有需要的后缀版本
            new_tags = [tag]
            for i in range(1, count):
                new_tags.append(f"{tag}-{i}")
            duplicate_tag_info[tag] = new_tags
            log(f"  发现重复 tag '{tag}' 出现 {count} 次，将重命名为: {new_tags}")
    
    return duplicate_tag_info


def expand_subscription_item(item, subscriptions_nodes, duplicate_tag_info, include_regex):
    """
    展开 Subscription 占位符为实际节点标签列表
    
    Args:
        item: Subscription 对象 {"type": "Subscription", "tag": "xxx"} 或 {"type": "Subscription", "tag": ["sub1", "sub2"]}
        subscriptions_nodes: dict，键为订阅名，值为节点列表
        duplicate_tag_info: dict, 重复 tag 的新 tag 信息 {tag: [新tag列表]}
        include_regex: include 正则表达式（可能为 None）
    
    Returns:
        tuple: (展开后的节点标签列表, 更新后的 duplicate_tag_info)
    """
    if not isinstance(item, dict) or item.get('type') != 'Subscription':
        return [item], duplicate_tag_info
    
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
                for node in filtered:
                    new_tag = get_next_new_tag(node['tag'], duplicate_tag_info)
                    result_tags.append(new_tag)
            except re.error as e:
                log(f"    错误: include 正则无效: {e}")
                for node in nodes:
                    new_tag = get_next_new_tag(node['tag'], duplicate_tag_info)
                    result_tags.append(new_tag)
        else:
            for node in nodes:
                new_tag = get_next_new_tag(node['tag'], duplicate_tag_info)
                result_tags.append(new_tag)
    
    return result_tags, duplicate_tag_info


def get_next_new_tag(original_tag, duplicate_tag_info):
    """从 duplicate_tag_info 中获取下一个新 tag"""
    if original_tag in duplicate_tag_info and duplicate_tag_info[original_tag]:
        return duplicate_tag_info[original_tag].pop(0)
    return original_tag


def process_outbounds(outbounds, subscriptions_nodes, duplicate_tag_info, default_include_regex=None):
    """
    处理 outbounds 数组，展开 Subscription 占位符
    
    Args:
        outbounds: outbounds 数组
        subscriptions_nodes: dict，键为订阅名，值为节点列表
        duplicate_tag_info: dict, 重复 tag 的新 tag 信息
        default_include_regex: 当前 outbound 的默认 include 正则（用于没有自己 include 的 Subscription）
    
    Returns:
        tuple: (处理后的 outbounds 数组, 更新后的 duplicate_tag_info)
    """
    if not isinstance(outbounds, list):
        return outbounds, duplicate_tag_info
    
    result = []
    for item in outbounds:
        if isinstance(item, dict) and item.get('type') == 'Subscription':
            # 每个 Subscription 使用自己的 include_regex（如果有）
            # 如果 Subscription 没有自己的 include，使用父级 outbound 的 default_include_regex
            sub_include_regex = item.get('include')
            effective_include_regex = sub_include_regex if sub_include_regex else default_include_regex
            
            # 展开 Subscription，插入节点标签
            expanded, duplicate_tag_info = expand_subscription_item(
                item, subscriptions_nodes, duplicate_tag_info, effective_include_regex
            )
            result.extend(expanded)
        else:
            # 保留其他项（字符串、对象等）
            result.append(item)
    
    return result, duplicate_tag_info


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
    
    # ========== 步骤 1: 收集所有节点 ==========
    all_nodes = []
    for nodes in subscriptions_nodes.values():
        all_nodes.extend(nodes)
    
    # ========== 步骤 2: 统计重复 tag ==========
    duplicate_tag_info = build_duplicate_tag_info(all_nodes)
    
    if not duplicate_tag_info:
        log("没有发现重复 tag")
    else:
        log(f"发现 {len(duplicate_tag_info)} 个重复 tag，开始分配新名称")
    
    # ========== 步骤 3: 根据 duplicate_tag_info 更新所有节点的 tag ==========
    for node in all_nodes:
        if isinstance(node, dict) and 'tag' in node:
            original_tag = node['tag']
            new_tag = get_next_new_tag(original_tag, duplicate_tag_info)
            if original_tag != new_tag:
                log(f"  更新节点 tag: {original_tag} -> {new_tag}")
            node['tag'] = new_tag
    
    # duplicate_tag_info 可能还有剩余（未被节点使用的版本）
    # 这部分会在 Subscription 展开时使用
    
    # ========== 步骤 4: 修复 tls.insecure ==========
    fixed = fix_tls_insecure(all_nodes)
    log(f"已设置 {fixed} 个节点的 tls.insecure = true")
    
    # ========== 步骤 5: 处理所有 outbounds（展开 Subscription）==========
    total_subscription_count = 0
    
    for outbound in config['outbounds']:
        if not isinstance(outbound, dict):
            continue
        
        # 只有 selector 和 urltest 类型需要处理 outbounds 列表
        outbound_type = outbound.get('type', '')
        if outbound_type not in ('selector', 'urltest'):
            continue
        
        outbounds_list = outbound.get('outbounds', [])
        if not isinstance(outbounds_list, list):
            continue
        
        # 获取当前 outbound 的 include 正则
        include_regex = outbound.get('include')
        
        # 展开 Subscription（传入 duplicate_tag_info）
        processed, duplicate_tag_info = process_outbounds(
            outbounds_list, subscriptions_nodes, duplicate_tag_info, include_regex
        )
        outbound['outbounds'] = processed
        
        # 统计 Subscription 展开的节点数
        subscription_count = sum(1 for item in outbounds_list 
                                  if isinstance(item, dict) and item.get('type') == 'Subscription')
        total_subscription_count += subscription_count
    
    log(f"处理了 {total_subscription_count} 个 Subscription 占位符")
    
    # ========== 步骤 6: 移除所有 include 字段 ==========
    remove_include_field(config)
    
    # ========== 步骤 7: 将代理节点添加到 outbounds 末尾 ==========
    config['outbounds'].extend(all_nodes)
    log(f"已添加 {len(all_nodes)} 个代理节点到配置")
    
    # ========== 步骤 8: 处理空 outbound 的兼容性问题 ==========
    for outbound in config['outbounds']:
        if not isinstance(outbound, dict):
            continue
        
        # 只有 selector 和 urltest 类型需要有 outbounds 列表
        outbound_type = outbound.get('type', '')
        if outbound_type not in ('selector', 'urltest'):
            continue
        
        outbounds_list = outbound.get('outbounds', [])
        if not isinstance(outbounds_list, list) or len(outbounds_list) == 0:
            outbound['outbounds'] = ['COMPATIBLE']
            log(f"  {outbound.get('tag')} -> 空 outbound，添加 COMPATIBLE")
    
    # ========== 步骤 9: 添加 COMPATIBLE outbound 定义 ==========
    has_compatible = any(
        isinstance(o, dict) and o.get('tag') == 'COMPATIBLE'
        for o in config['outbounds']
    )
    if not has_compatible:
        config['outbounds'].append({
            "tag": "COMPATIBLE",
            "type": "direct"
        })
        log("已添加 COMPATIBLE outbound 定义")
    
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
