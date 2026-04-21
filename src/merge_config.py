#!/usr/bin/env python3
"""
Sing-box 配置合并脚本

将 sing-box 订阅节点合并到 sing-box 配置模板中，生成最终可用的配置文件。

功能：
1. 读取配置模板
2. 将订阅节点按规则分配到不同的 outbound selector 中
3. 设置 tls.insecure = true 以支持自签名证书
4. 处理空 outbound 的兼容性问题
"""

import json
import re
import os
import sys


def log(msg):
    """日志输出到 stderr"""
    import sys
    print(f"[Merge] {msg}", file=sys.stderr)


def parse_outbound_rules(outbound_str):
    """
    解析 outbound 规则字符串
    格式: "🕳节点选择器名称🏷节点标签正则🕳另一个选择器🏷另一个正则"
    """
    if not outbound_str:
        return []
    
    rules = []
    parts = outbound_str.split('🕳')
    
    for part in parts:
        if not part:
            continue
        
        if '🏷' in part:
            outbound_pattern, tag_pattern = part.split('🏷', 1)
        else:
            outbound_pattern = part
            tag_pattern = '.*'
        
        rules.append((outbound_pattern.strip(), tag_pattern.strip()))
    
    return rules


def create_tag_regex(tag_pattern):
    """创建节点标签匹配正则"""
    pattern = tag_pattern.replace('ℹ️', '')
    flags = re.IGNORECASE if 'ℹ️' in tag_pattern else 0
    return re.compile(pattern, flags)


def create_outbound_regex(outbound_pattern):
    """创建 outbound 选择器匹配正则"""
    pattern = outbound_pattern.replace('ℹ️', '')
    flags = re.IGNORECASE if 'ℹ️' in outbound_pattern else 0
    return re.compile(pattern, flags)


def fix_tls_insecure(proxies):
    """遍历所有节点，将 tls.insecure 设为 true"""
    fixed_count = 0
    for proxy in proxies:
        if 'tls' in proxy and isinstance(proxy['tls'], dict):
            proxy['tls']['insecure'] = True
            fixed_count += 1
    return fixed_count


def merge_config(template_config, proxies, outbound_rules_str):
    """
    合并配置
    
    Args:
        template_config: 配置模板字典
        proxies: sing-box 格式的代理节点列表
        outbound_rules_str: outbound 规则字符串
    
    Returns:
        合并后的配置字典
    """
    # 深拷贝配置模板
    config = json.loads(json.dumps(template_config))
    
    # 确保 outbounds 是列表
    if 'outbounds' not in config:
        config['outbounds'] = []
    
    # 修复所有节点的 tls.insecure
    fixed = fix_tls_insecure(proxies)
    log(f"已设置 {fixed} 个节点的 tls.insecure = true")
    
    # 解析 outbound 规则
    outbound_rules = parse_outbound_rules(outbound_rules_str)
    log(f"解析到 {len(outbound_rules)} 条 outbound 规则")
    
    # 将节点插入到对应的 outbound selector 中
    for outbound_rule in outbound_rules:
        outbound_pattern, tag_pattern = outbound_rule
        outbound_regex = create_outbound_regex(outbound_pattern)
        tag_regex = create_tag_regex(tag_pattern)
        
        # 找到匹配的选择器
        for outbound in config['outbounds']:
            if outbound_regex.search(outbound.get('tag', '')):
                if not isinstance(outbound.get('outbounds'), list):
                    outbound['outbounds'] = []
                
                # 获取匹配的节点标签
                matched_tags = [p['tag'] for p in proxies if tag_regex.search(p.get('tag', ''))]
                outbound['outbounds'].extend(matched_tags)
                log(f"  {outbound.get('tag')} -> 插入 {len(matched_tags)} 个节点 (匹配 {tag_pattern})")
    
    # 检查并修复空的 outbounds
    compatible_outbound = {'tag': 'COMPATIBLE', 'type': 'direct'}
    has_compatible = any(o.get('tag') == 'COMPATIBLE' for o in config['outbounds'])
    
    for outbound_rule in outbound_rules:
        outbound_pattern, tag_pattern = outbound_rule
        outbound_regex = create_outbound_regex(outbound_pattern)
        
        for outbound in config['outbounds']:
            if outbound_regex.search(outbound.get('tag', '')):
                if not isinstance(outbound.get('outbounds'), list) or len(outbound.get('outbounds', [])) == 0:
                    if not has_compatible:
                        config['outbounds'].append(compatible_outbound)
                        has_compatible = True
                    outbound['outbounds'] = outbound.get('outbounds', [])
                    outbound['outbounds'].append('COMPATIBLE')
                    log(f"  {outbound.get('tag')} -> 空 outbound，添加 COMPATIBLE")
    
    # 将所有代理节点添加到 outbounds 末尾
    config['outbounds'].extend(proxies)
    log(f"已添加 {len(proxies)} 个代理节点到配置")
    
    return config


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



def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='合并 sing-box 订阅到配置模板')
    parser.add_argument('template', help='配置模板文件路径 (.json 或 .jsonc)')
    parser.add_argument('subscription', help='sing-box 订阅文件路径')
    parser.add_argument('-o', '--output', help='输出文件路径 (默认输出到 stdout)')
    parser.add_argument('--outbound-rules', 
                       default='🕳✈️ Telegram🕳📹 YouTube🕳🎈 自动选择🕳🇭🇰 香港节点🏷^(?=.*(港|HK|hk|Hong Kong|HongKong|hongkong)).*$🕳🇹🇼 台湾节点🏷^(?=.*(台|新北|彰化|TW|Taiwan)).*$🕳🇯🇵 日本节点🏷^(?=.*(日本|川日|东京|大阪|泉日|埼玉|沪日|深日|[^-]日|JP|Japan)).*$🕳🇸🇬 新加坡节点🏷^(?=.*(新加坡|坡|狮城|SG|Singapore)).*$🕳🇺🇸 美国节点🏷^(?=.*(美|波特兰|达拉斯|俄勒冈|凤凰城|费利蒙|硅谷|拉斯维加斯|洛杉矶|圣何塞|圣克拉拉|西雅图|芝加哥|US|United States)).*$🕳🇪🇺 欧洲节点🏷^(?=.*(奥|比|保|克罗地亚|塞|捷|丹|爱沙|芬|法|德|希|匈|爱尔|意|拉|立|卢|马耳他|荷|波|葡|西班牙|俄罗斯|斯洛伐|斯洛文|瑞|英|冰岛|挪威|瑞士|列支|摩纳|梵蒂|圣马|黑山|阿尔巴|北马其|波斯尼|科索沃|🇷🇺|🇦🇹|🇧🇪|🇧🇬|🇭🇷|🇨🇾|🇨🇿|🇩🇰|🇪🇪|🇫🇮|🇫🇷|🇩🇪|🇬🇷|🇭🇺|🇮🇪|🇮🇹|🇱🇻|🇱🇹|🇱🇺|🇲🇹|🇳🇱|🇳🇴|🇵🇱|🇵🇹|🇷🇴|🇷🇸|🇸🇰|🇸🇮|🇪🇸|🇸🇪|🇨🇭|🇬🇧|🇮🇸|🇦🇱|🇲🇰|🇲🇪|🇽🇰|🇸🇲|🇻🇦|🇱🇮|🇧🇦|MOW|LED|SVO|CDG|FRA|AMS|MAD|BCN|FCO|MUC|BRU|VIE|ZRH|OSL|CPH|ARN|HEL|DUB)).*$🕳🧭 其它地区🏷^(?!.*(港|HK|hk|Hong Kong|HongKong|hongkong|日本|川日|东京|大阪|泉日|埼玉|沪日|深日|[^-]日|JP|Japan|美|波特兰|达拉斯|俄勒冈|凤凰城|费利蒙|硅谷|拉斯维加斯|洛杉矶|圣何塞|圣克拉拉|西雅图|芝加哥|US|United States|台|新北|彰化|TW|Taiwan|新加坡|坡|狮城|SG|Singapore|灾|网易|Netease|套餐|重置|剩余|到期|订阅|群|账户|流量|有效期|时间|官网)).*$',
                       help='outbound 规则字符串')
    
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
    
    # 获取代理节点
    proxies = subscription if isinstance(subscription, list) else subscription.get('outbounds', [])
    if not proxies:
        log("警告: 未找到代理节点")
        proxies = []
    else:
        log(f"找到 {len(proxies)} 个代理节点")
    
    # 合并配置
    merged = merge_config(template, proxies, args.outbound_rules)
    
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
