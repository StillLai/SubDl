#!/usr/bin/env python3
"""
Subscription Downloader and Gist Uploader
"""

import os
import sys
import base64
import re
import time
import json
import subprocess
import tempfile
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

import requests


def get_env_var(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ValueError(f"环境变量 {name} 未设置")
    return value


def parse_flow_info(headers):
    """从响应头解析流量信息"""
    flow_header = headers.get('subscription-userinfo', '')
    if not flow_header:
        return None
    
    upload = re.search(r'upload=(\d+)', flow_header)
    download = re.search(r'download=(\d+)', flow_header)
    total = re.search(r'total=(\d+)', flow_header)
    expire = re.search(r'expire=(\d+)', flow_header)
    
    return {
        'upload': int(upload.group(1)) if upload else 0,
        'download': int(download.group(1)) if download else 0,
        'total': int(total.group(1)) if total else 0,
        'expire': int(expire.group(1)) if expire else None,
    }


def format_bytes(bytes_val):
    """格式化字节数"""
    if bytes_val == 0:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_idx = 0
    while bytes_val >= 1024 and unit_idx < len(units) - 1:
        bytes_val /= 1024
        unit_idx += 1
    return f"{bytes_val:.2f} {units[unit_idx]}"


def format_expire(timestamp):
    """格式化到期时间"""
    if not timestamp:
        return "无"
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d")
    except:
        return "无"


def get_status(flow_info):
    """获取状态"""
    if not flow_info:
        return "❓ 无信息"
    
    total = flow_info.get('total', 0)
    used = flow_info.get('upload', 0) + flow_info.get('download', 0)
    expire = flow_info.get('expire')
    
    if expire and expire < time.time():
        return "❌ 已过期"
    if total > 0 and used >= total:
        return "❌ 流量用完"
    if expire and expire - time.time() < 7 * 24 * 3600:
        return "⚠️ 即将到期"
    return "✅ 正常"


def download_subscription(url, user_agent, timeout=30000):
    """下载订阅内容"""
    headers = {"User-Agent": user_agent}
    response = requests.get(url, headers=headers, timeout=timeout / 1000, allow_redirects=True)
    response.raise_for_status()
    content = response.text
    
    try:
        cleaned = content.strip().replace(" ", "").replace("\n", "").replace("\r", "")
        if re.match(r'^[A-Za-z0-9+/=]+$', cleaned):
            decoded = base64.b64decode(cleaned + "=" * (4 - len(cleaned) % 4))
            content = decoded.decode("utf-8")
    except Exception:
        pass
    
    return content, parse_flow_info(response.headers)


def parse_subscriptions():
    """解析订阅配置"""
    subscriptions = []
    for env_name in ["SUB_URL"] + [f"SUB_URL_{i}" for i in range(1, 10)]:
        value = os.environ.get(env_name, "").strip()
        if not value:
            continue
        if "|" in value:
            name, url = value.split("|", 1)
            name, url = name.strip(), url.strip()
        else:
            url = value
            name = extract_name_from_url(url)
        if name and url:
            subscriptions.append({"name": name, "url": url, "filename": f"{name}.yaml"})
    return subscriptions


def extract_name_from_url(url):
    try:
        domain = urlparse(url).netloc.replace("www.", "").split(":")[0]
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', domain)
        return name[:50]
    except:
        return f"sub_{int(time.time())}"


def upload_to_gist(github_token, gist_id, files):
    headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
    gist_files = {filename: {"content": content} for filename, content in files.items()}
    
    try:
        if not gist_id:
            print(f"    创建新的 Gist...")
            response = requests.post("https://api.github.com/gists", headers=headers, json={
                "description": "SubDl Subscriptions", "public": False, "files": gist_files
            }, timeout=30)
            response.raise_for_status()
            new_id = response.json()["id"]
            print(f"    ✓ 创建成功，Gist ID: {new_id}")
            return new_id
        
        print(f"    更新 Gist: {gist_id}")
        response = requests.patch(f"https://api.github.com/gists/{gist_id}", headers=headers, json={"files": gist_files}, timeout=30)
        response.raise_for_status()
        print(f"    ✓ 更新成功")
        return gist_id
    except requests.exceptions.HTTPError as e:
        print(f"    ✗ Gist API 错误: {e}")
        print(f"      响应: {e.response.text if hasattr(e, 'response') else 'N/A'}")
        raise
    except Exception as e:
        print(f"    ✗ Gist 上传异常: {e}")
        raise


def parse_cron_interval():
    """从 workflow 文件解析 cron 间隔"""
    workflow_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.github', 'workflows', 'update-subscriptions.yml')
    try:
        with open(workflow_path, 'r', encoding='utf-8') as f:
            content = f.read()
            match = re.search(r"cron:\s*['\"](\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)['\"]", content)
            if match:
                minute, hour, day, month, weekday = match.groups()
                if minute != '*' and hour == '*':
                    return "每小时"
                elif minute == '*' and hour == '*':
                    return "每分钟"
                elif hour.startswith('*/'):
                    return f"每 {hour[2:]} 小时"
    except:
        pass
    return "每小时"


def generate_readme(subscription_info):
    """生成 README 内容"""
    interval = parse_cron_interval()
    lines = [
        "# SubDl", "",
        f"> 最后更新: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S CST')}", "",
        "## 订阅状态", "",
        "| 订阅 | 总流量 | 已用 | 剩余 | 到期时间 | 状态 |",
        "|------|--------|------|------|----------|------|",
    ]
    
    for info in subscription_info:
        flow = info.get('flow', {})
        total = flow.get('total', 0)
        used = flow.get('upload', 0) + flow.get('download', 0)
        lines.append(f"| {info['name']} | {format_bytes(total)} | {format_bytes(used)} | {format_bytes(total - used if total > 0 else 0)} | {format_expire(flow.get('expire'))} | {get_status(flow)} |")
    
    lines.extend([
        "", "## 快速配置", "",
        "1. Fork 本仓库",
        "2. 在 Settings → Secrets → Actions 中添加:",
        "   - `GH_TOKEN`: GitHub Token (需要 gist 权限)",
        "   - `GIST_ID`: Gist ID（可选，首次运行后会自动创建并输出）",
        "   - `SUB_URL`: 订阅链接 (`名称|URL` 格式)",
        "   - `SUB_URL_1`, `SUB_URL_2`...: 更多订阅（可选）",
        "3. 在 Actions → Update Subscriptions 中点击 Run workflow", "",
        "## 说明", "",
        f"- {interval}自动更新订阅",
        "- 订阅内容上传到 Gist，不保存在仓库",
        "- `sing-box-config.json` 是可直接使用的完整sing-box配置文件",
        "- 参考 [sub-store](https://github.com/sub-store-org/Sub-Store) 实现", "",
    ])
    return "\n".join(lines)


def convert_to_singbox(clash_content, script_dir):
    """将Clash配置转换为Sing-box格式"""
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            f.write(clash_content)
            temp_file = f.name
        try:
            convert_script = os.path.join(script_dir, 'convert.mjs')
            result = subprocess.run(['node', convert_script, 'convert', temp_file], capture_output=True, text=True, encoding='utf-8')
            if result.returncode != 0:
                print(f"  ✗ 转换失败: {result.stderr}")
                return None
            return json.loads(result.stdout)
        finally:
            os.unlink(temp_file)
    except Exception as e:
        print(f"  ✗ 转换异常: {e}")
        return None


def merge_singbox_config(subs_nodes_dict, script_dir, template_path=None):
    """将多个sing-box订阅节点合并到配置模板"""
    try:
        if template_path is None:
            template_path = os.path.join(script_dir, '..', 'template', 'sing-box_template.jsonc')
        if not os.path.exists(template_path):
            print(f"  ✗ 配置模板不存在: {template_path}")
            return None
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(subs_nodes_dict, f)
            sub_temp_file = f.name
        
        try:
            merge_script = os.path.join(script_dir, 'merge_config.py')
            result = subprocess.run(['python', merge_script, template_path, sub_temp_file], capture_output=True, text=True, encoding='utf-8')
            if result.returncode != 0:
                print(f"  ✗ 合并失败: {result.stderr}")
                return None
            stdout = result.stdout.strip()
            if not stdout:
                print(f"  ✗ 合并脚本没有输出")
                return None
            return json.loads(stdout)
        finally:
            os.unlink(sub_temp_file)
    except Exception as e:
        print(f"  ✗ 合并异常: {e}")
        return None


def merge_all_templates(subs_nodes_dict, script_dir):
    """遍历所有模板文件并生成配置文件"""
    template_dir = os.path.join(script_dir, '..', 'template')
    if not os.path.exists(template_dir):
        print(f"  ✗ 模板目录不存在: {template_dir}")
        return {}
    
    merged_configs = {}
    template_files = [f for f in os.listdir(template_dir) if f.endswith(('.jsonc', '.json'))]
    
    if not template_files:
        print(f"  ✗ 模板目录中没有找到模板文件")
        return {}
    
    print(f"  找到 {len(template_files)} 个模板文件")
    
    for template_file in template_files:
        template_path = os.path.join(template_dir, template_file)
        # 将文件名中的 "template" 替换为 "config"，扩展名改为 .json
        # 先分离扩展名，避免处理异常
        if template_file.endswith('.jsonc'):
            base_name = template_file[:-6]  # .jsonc 是 6 个字符
        elif template_file.endswith('.json'):
            base_name = template_file[:-5]  # .json 是 5 个字符
        else:
            base_name = template_file
        # 替换 template -> config
        config_filename = base_name.replace('template', 'config') + '.json'
        
        print(f"  → 处理模板: {template_file}")
        merged_config = merge_singbox_config(subs_nodes_dict, script_dir, template_path)
        if merged_config:
            merged_configs[config_filename] = json.dumps(merged_config, indent=2, ensure_ascii=False)
            total_nodes = sum(len(nodes) for nodes in subs_nodes_dict.values())
            print(f"    ✓ 生成 {config_filename} ({len(merged_configs[config_filename])} 字节, {total_nodes} 个节点)")
    
    return merged_configs


def load_jsonc(filepath):
    """加载 JSONC 文件（支持注释）"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    lines = []
    for line in content.split('\n'):
        stripped = line.lstrip()
        if stripped.startswith('//'):
            indent = line[:len(line) - len(line.lstrip())]
            lines.append(indent)
        else:
            lines.append(line)
    return json.loads('\n'.join(lines))


def generate_notun_template(script_dir):
    """生成不含 tun inbound 的模板文件"""
    try:
        template_path = os.path.join(script_dir, '..', 'template', 'sing-box_template.jsonc')
        output_path = os.path.join(script_dir, '..', 'template', 'sing-box_template_noTun.jsonc')
        
        template = load_jsonc(template_path)
        
        if 'inbounds' in template and isinstance(template['inbounds'], list):
            original_count = len(template['inbounds'])
            template['inbounds'] = [
                inbound for inbound in template['inbounds']
                if not (isinstance(inbound, dict) and inbound.get('type') == 'tun')
            ]
            removed_count = original_count - len(template['inbounds'])
            print(f"  ✓ 已移除 {removed_count} 个 tun inbound")
        
        output_content = json.dumps(template, indent=2, ensure_ascii=False)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_content)
        print(f"  ✓ 已生成 noTun 模板: template/sing-box_template_noTun.jsonc")
        return output_content
    except Exception as e:
        print(f"  ✗ 生成 noTun 模板异常: {e}")
        return None


def main():
    print("=" * 60)
    print("SubDl - Subscription Downloader")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    github_token = get_env_var("GH_TOKEN", required=True)
    gist_id = get_env_var("GIST_ID", default="")
    user_agent = get_env_var("USER_AGENT", default="clash-verge/v2.4.4")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("\n→ 生成不含 tun inbound 的模板...")
    notun_template = generate_notun_template(script_dir)
    
    subscriptions = parse_subscriptions()
    if not subscriptions:
        print("错误: 未找到订阅配置")
        sys.exit(1)
    
    print(f"\n找到 {len(subscriptions)} 个订阅\n")
    
    files = {}
    subscription_info = []
    failed = []
    
    for sub in subscriptions:
        print(f"下载: {sub['name']}")
        try:
            content, flow_info = download_subscription(sub["url"], user_agent)
            files[sub["filename"]] = content
            subscription_info.append({"name": sub["name"], "flow": flow_info})
            print(f"  ✓ 成功 ({len(content)} 字节)")
            
            print(f"  → 转换为Sing-box格式...")
            singbox_config = convert_to_singbox(content, script_dir)
            if singbox_config:
                singbox_nodes = singbox_config if isinstance(singbox_config, list) else singbox_config.get('outbounds', [])
                singbox_filename = f"{sub['name']}-singbox.json"
                files[singbox_filename] = json.dumps(singbox_config, indent=2, ensure_ascii=False)
                print(f"  ✓ 转换成功 ({len(files[singbox_filename])} 字节, {len(singbox_nodes)} 个节点)")
        except Exception as e:
            print(f"  ✗ 失败: {e}")
            failed.append({"name": sub["name"], "error": str(e)})
    
    if not files:
        print("错误: 所有订阅下载失败")
        sys.exit(1)
    
    if notun_template:
        files["template/sing-box_template_noTun.jsonc"] = notun_template
    
    print(f"\n→ 使用 {len(subscriptions)} 个订阅生成sing-box配置...")
    subs_nodes_dict = {}
    for sub in subscriptions:
        try:
            content, _ = download_subscription(sub["url"], user_agent)
            singbox_config = convert_to_singbox(content, script_dir)
            if singbox_config:
                nodes = singbox_config if isinstance(singbox_config, list) else singbox_config.get('outbounds', [])
                subs_nodes_dict[sub['name']] = nodes
                print(f"  → 订阅 '{sub['name']}': {len(nodes)} 个节点")
        except Exception as e:
            print(f"  ✗ {sub['name']} 获取节点失败: {e}")
    
    if subs_nodes_dict:
        # 遍历所有模板文件生成配置文件
        merged_configs = merge_all_templates(subs_nodes_dict, script_dir)
        for filename, content in merged_configs.items():
            files[filename] = content
        if merged_configs:
            print(f"  ✓ 共生成 {len(merged_configs)} 个配置文件")
    
        readme_content = generate_readme(subscription_info)
    with open("README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)
    print("\n✓ README 已更新")
    
    print(f"\n上传 {len(files)} 个文件到 Gist...")
    new_gist_id = upload_to_gist(github_token, gist_id, files)
    
    if new_gist_id != gist_id:
        print(f"\n重要提示: 已创建新的 Gist ID: {new_gist_id}")
        print("请在 Repository secrets 中设置 GIST_ID")
    
    print(f"\n完成! 成功处理 {len(files)} 个订阅")
    if failed:
        print(f"\n警告: {len(failed)} 个订阅下载失败")
        sys.exit(2)


if __name__ == "__main__":
    main()
