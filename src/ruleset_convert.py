import pandas as pd
import os
import json
import requests
import yaml
import ipaddress
import re

def read_json_from_url(url):
    """下载并解析 sing-box JSON 格式的规则集"""
    response = requests.get(url)
    response.raise_for_status()
    json_data = json.loads(response.text)
    return json_data

def is_singbox_ruleset(json_data):
    """判断 JSON 数据是否为 sing-box 规则集格式"""
    if isinstance(json_data, dict):
        return 'version' in json_data and 'rules' in json_data
    return False

def read_yaml_from_url(url):
    response = requests.get(url)
    response.raise_for_status()
    yaml_data = yaml.safe_load(response.text)
    return yaml_data

def read_list_from_url(url):
    df = pd.read_csv(url, header=None, names=['pattern', 'address', 'other'], on_bad_lines='warn')
    return df

def is_ipv4_or_ipv6(address):
    try:
        ipaddress.IPv4Network(address)
        return 'ipv4'
    except ValueError:
        try:
            ipaddress.IPv6Network(address)
            return 'ipv6'
        except ValueError:
            return None

def is_android_package_name(text):
    """
    判断是否为安卓程序包名
    安卓包名通常符合以下特征：
    1. 包含点分隔符（如 com.example.app）
    2. 每部分以字母开头，包含字母、数字、下划线
    3. 通常以 com., org., net. 等常见域名开头
    
    同时排除其他系统的程序特征：
    - 以 .exe, .dll, .app, .dmg 等结尾的文件
    - 包含路径分隔符（/ 或 \）的文件路径
    - 其他明显不是包名的格式
    """
    if not text or not isinstance(text, str):
        return False
    
    # 排除明显是其他系统的程序
    other_system_extensions = ['.exe', '.dll', '.app', '.dmg', '.msi', '.deb', '.rpm', '.pkg']
    if any(text.lower().endswith(ext) for ext in other_system_extensions):
        return False
    
    # 排除包含路径分隔符的路径
    if '/' in text or '\\' in text:
        return False
    
    # 排除包含空格的文件名
    if ' ' in text:
        return False
    
    # 基本格式检查：包含点分隔符
    if '.' not in text:
        return False
    
    # 检查每部分是否符合包名规范
    parts = text.split('.')
    for part in parts:
        if not part:  # 空部分
            return False
        if not part[0].isalpha():  # 每部分必须以字母开头
            return False
        if not re.match(r'^[a-zA-Z0-9_]+$', part):  # 只包含字母、数字、下划线
            return False
    
    # 常见的包名前缀
    common_prefixes = ['com', 'org', 'net', 'edu', 'gov', 'mil', 'android', 'google']
    if parts[0] in common_prefixes:
        return True
    
    # 如果不符合常见前缀，但格式正确，也认为是包名
    return len(parts) >= 2  # 至少有两部分

def parse_and_convert_to_dataframe(link):
    if link.endswith('.yaml') or link.endswith('.txt'):
        try:
            yaml_data = read_yaml_from_url(link)
            rows = []
            if not isinstance(yaml_data, str):
                items = yaml_data.get('payload', [])
            else:
                lines = yaml_data.splitlines()
                line_content = lines[0]
                items = line_content.split()
            for item in items:
                address = item.strip("'")
                if ',' not in item:
                    if is_ipv4_or_ipv6(item):
                        pattern = 'IP-CIDR'
                    else:
                        if address.startswith('+') or address.startswith('.'):
                            pattern = 'DOMAIN-SUFFIX'
                            # 只去掉+号，保留点号
                            if address.startswith('+'):
                                address = address[1:]
                        else:
                            pattern = 'DOMAIN'
                else:
                    pattern, address = item.split(',', 1)  
                rows.append({'pattern': pattern.strip(), 'address': address.strip(), 'other': None})
            df = pd.DataFrame(rows, columns=['pattern', 'address', 'other'])
        except:
            df = read_list_from_url(link)
    else:
        df = read_list_from_url(link)
    return df

def sort_dict(obj):
    if isinstance(obj, dict):
        sorted_keys = sorted(obj.keys())
        # 将 "version" 键移到最前面
        if "version" in sorted_keys:
            sorted_keys.remove("version")
            sorted_keys.insert(0, "version")
        return {k: sort_dict(obj[k]) for k in sorted_keys}
    elif isinstance(obj, list) and all(isinstance(elem, dict) for elem in obj):
        return sorted([sort_dict(x) for x in obj], key=lambda d: sorted(d.keys())[0])
    elif isinstance(obj, list):
        return sorted(sort_dict(x) for x in obj)
    else:
        return obj

def parse_list_file(link, output_directory):
    os.makedirs(output_directory, exist_ok=True)
    file_name = os.path.join(output_directory, f"{os.path.basename(link).split('.')[0]}.json")
    
    # 如果是 .json 链接，先检查是否为 sing-box 规则集格式
    if link.endswith('.json'):
        try:
            json_data = read_json_from_url(link)
            if is_singbox_ruleset(json_data):
                # 已经是 sing-box 规则集格式，直接下载并保存
                with open(file_name, 'w', encoding='utf-8') as output_file:
                    json.dump(sort_dict(json_data), output_file, ensure_ascii=False, indent=2)
                
                # SRS 输出到 ruleset_srs 目录
                srs_filename = os.path.basename(file_name).replace(".json", ".srs")
                srs_path = os.path.join("./ruleset_srs/", srs_filename)
                os.makedirs("./ruleset_srs/", exist_ok=True)
                os.system(f"sing-box rule-set compile --output {srs_path} {file_name}")
                return file_name
        except Exception as e:
            print(f"处理 JSON 文件失败 {link}: {e}")
            # 如果不是 sing-box 格式或下载失败，继续使用原有逻辑处理

    df = parse_and_convert_to_dataframe(link)

    df = df[~df['pattern'].str.contains('#')].reset_index(drop=True)

    # 恢复原始映射字典，但处理重复键的情况
    map_dict = {
        'DOMAIN-SUFFIX': 'domain_suffix', 
        'HOST-SUFFIX': 'domain_suffix', 
        'DOMAIN': 'domain', 
        'HOST': 'domain', 
        'host': 'domain',
        'DOMAIN-KEYWORD': 'domain_keyword', 
        'HOST-KEYWORD': 'domain_keyword', 
        'host-keyword': 'domain_keyword', 
        'IP-CIDR': 'ip_cidr',
        'ip-cidr': 'ip_cidr', 
        'IP-CIDR6': 'ip_cidr', 
        'IP6-CIDR': 'ip_cidr',
        'SRC-IP-CIDR': 'source_ip_cidr', 
        'GEOIP': 'geoip', 
        'DST-PORT': 'port',
        'SRC-PORT': 'source_port', 
        'URL-REGEX': 'domain_regex', 
        'PROCESS-NAME': 'process_name'
    }
    
    # 筛选出支持的 pattern
    df_filtered = df[df['pattern'].isin(map_dict.keys())].reset_index(drop=True)
    
    # 基础映射
    df_with_mappings = df_filtered.copy()
    df_with_mappings['mapped_pattern'] = df_with_mappings['pattern'].map(map_dict)
    
    # PROCESS-NAME 特殊处理：根据地址内容判断是安卓包名还是普通进程名
    process_mask = df_with_mappings['pattern'] == 'PROCESS-NAME'
    df_with_mappings.loc[process_mask, 'mapped_pattern'] = df_with_mappings.loc[process_mask, 'address'].apply(
        lambda x: 'package_name' if is_android_package_name(x) else 'process_name'
    )
    
    df_with_mappings = df_with_mappings.drop_duplicates().reset_index(drop=True)

    result_rules = {"version": 4, "rules": []}
    domain_suffix_set = set()

    # 先收集所有 domain_suffix 的地址
    for pattern, group in df_with_mappings.groupby('mapped_pattern'):
        if pattern == 'domain_suffix':
            addresses = group['address'].tolist()
            domain_suffix_set.update([address.strip() for address in addresses])

    domain_entries = []

    # 按映射后的模式分组处理
    for pattern, group in df_with_mappings.groupby('mapped_pattern'):
        addresses = group['address'].tolist()
        
        if pattern == 'domain_suffix':
            rule_entry = {pattern: [address.strip() for address in addresses]}
            result_rules["rules"].append(rule_entry)
        elif pattern == 'domain':
            # 过滤掉已存在于 domain_suffix 中的域名
            filtered_addresses = [address.strip() for address in addresses if address.strip() not in domain_suffix_set]
            domain_entries.extend(filtered_addresses)
        elif pattern in ['port', 'source_port']:
            # 特殊处理端口字段，将端口号转换为数字
            port_numbers = []
            for address in addresses:
                address = address.strip()
                try:
                    port_numbers.append(int(address))
                except ValueError:
                    port_numbers.append(address)
            rule_entry = {pattern: port_numbers}
            result_rules["rules"].append(rule_entry)
        else:
            rule_entry = {pattern: [address.strip() for address in addresses]}
            result_rules["rules"].append(rule_entry)
    
    # 对 domain 字段去重并插入到 rules 最前面
    domain_entries = list(set(domain_entries))
    if domain_entries:
        result_rules["rules"].insert(0, {'domain': domain_entries})

    with open(file_name, 'w', encoding='utf-8') as output_file:
        json.dump(sort_dict(result_rules), output_file, ensure_ascii=False, indent=2)

    # SRS 输出到 ruleset_srs 目录
    srs_filename = os.path.basename(file_name).replace(".json", ".srs")
    srs_path = os.path.join("./ruleset_srs/", srs_filename)
    os.makedirs("./ruleset_srs/", exist_ok=True)
    os.system(f"sing-box rule-set compile --output {srs_path} {file_name}")
    return file_name

with open("ruleset_source.txt", 'r') as links_file:
    links = links_file.read().splitlines()

links = [l for l in links if l.strip() and not l.strip().startswith("#")]

output_dir = "./ruleset_json/"
result_file_names = []

for link in links:
    result_file_name = parse_list_file(link, output_directory=output_dir)
    result_file_names.append(result_file_name)

for file_name in result_file_names:
    print(file_name)