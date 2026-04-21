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
    
    # 解析 upload, download, total, expire
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
    
    # 检查是否过期
    if expire and expire < time.time():
        return "❌ 已过期"
    
    # 检查流量是否用完
    if total > 0 and used >= total:
        return "❌ 流量用完"
    
    # 检查是否即将到期（7天内）
    if expire and expire - time.time() < 7 * 24 * 3600:
        return "⚠️ 即将到期"
    
    return "✅ 正常"


def download_subscription(url, user_agent, timeout=30000):
    """下载订阅内容"""
    headers = {"User-Agent": user_agent}
    
    response = requests.get(
        url,
        headers=headers,
        timeout=timeout / 1000,
        allow_redirects=True
    )
    response.raise_for_status()
    
    content = response.text
    
    # 尝试 base64 解码
    try:
        cleaned = content.strip().replace(" ", "").replace("\n", "").replace("\r", "")
        if re.match(r'^[A-Za-z0-9+/=]+$', cleaned):
            decoded = base64.b64decode(cleaned + "=" * (4 - len(cleaned) % 4))
            content = decoded.decode("utf-8")
    except Exception:
        pass
    
    # 获取流量信息
    flow_info = parse_flow_info(response.headers)
    
    return content, flow_info


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
            subscriptions.append({
                "name": name,
                "url": url,
                "filename": f"{name}.yaml"
            })
    
    return subscriptions


def extract_name_from_url(url):
    try:
        domain = urlparse(url).netloc.replace("www.", "").split(":")[0]
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', domain)
        return name[:50]
    except:
        return f"sub_{int(time.time())}"


def upload_to_gist(github_token, gist_id, files):
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    gist_files = {filename: {"content": content} for filename, content in files.items()}
    
    if not gist_id:
        response = requests.post(
            "https://api.github.com/gists",
            headers=headers,
            json={
                "description": "SubDl Subscriptions",
                "public": False,
                "files": gist_files
            },
            timeout=30
        )
        return response.json()["id"]
    
    requests.patch(
        f"https://api.github.com/gists/{gist_id}",
        headers=headers,
        json={"files": gist_files},
        timeout=30
    )
    return gist_id


def parse_cron_interval():
    """从 workflow 文件解析 cron 间隔"""
    import os
    workflow_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.github', 'workflows', 'update-subscriptions.yml')
    try:
        with open(workflow_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # 匹配 cron: '55 * * * *'
            match = re.search(r"cron:\s*['\"](\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)['\"]", content)
            if match:
                minute, hour, day, month, weekday = match.groups()
                if minute != '*' and hour == '*':
                    return "每小时"
                elif minute == '*' and hour == '*':
                    return "每分钟"
                elif hour.startswith('*/'):
                    interval = hour[2:]
                    return f"每 {interval} 小时"
    except:
        pass
    return "每小时"


def generate_readme(subscription_info):
    """生成 README 内容"""
    interval = parse_cron_interval()
    lines = [
        "# SubDl",
        "",
        f"> 最后更新: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S CST')}",
        "",
        "## 订阅状态",
        "",
        "| 订阅 | 总流量 | 已用 | 剩余 | 到期时间 | 状态 |",
        "|------|--------|------|------|----------|------|",
    ]
    
    for info in subscription_info:
        flow = info.get('flow', {})
        total = flow.get('total', 0)
        used = flow.get('upload', 0) + flow.get('download', 0)
        remaining = total - used if total > 0 else 0
        expire = format_expire(flow.get('expire'))
        status = get_status(flow)
        
        lines.append(f"| {info['name']} | {format_bytes(total)} | {format_bytes(used)} | {format_bytes(remaining)} | {expire} | {status} |")
    
    lines.extend([
        "",
        "## 快速配置",
        "",
        "1. Fork 本仓库",
        "2. 在 Settings → Secrets → Actions 中添加:",
        "   - `GH_TOKEN`: GitHub Token (需要 gist 权限)",
        "   - `SUB_URL`: 订阅链接 (`名称|URL` 格式)",
        "   - `SUB_URL_1`, `SUB_URL_2`...: 更多订阅（可选）",
        "   - `SINGBOX_CONFIG_SUBS`: 用于生成sing-box配置的订阅，设为 `all` 使用全部订阅，或用逗号分隔订阅名称，如 `sub1,sub2`",
        "3. 在 Actions → Update Subscriptions 中点击 Run workflow",
        "",
        "## 说明",
        "",
        f"- {interval}自动更新订阅",
        "- 订阅内容上传到 Gist，不保存在仓库",
        "- `sing-box-config.json` 是可直接使用的完整sing-box配置文件",
        "- 参考 [sub-store](https://github.com/sub-store-org/Sub-Store) 实现",
        "",
    ])
    
    return "\n".join(lines)


def convert_to_singbox(clash_content, script_dir):
    """将Clash配置转换为Sing-box格式"""
    try:
        # 创建临时文件存储clash内容
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            f.write(clash_content)
            temp_file = f.name
        
        try:
            # 调用Node.js转换脚本（使用.mjs ES模块格式）
            convert_script = os.path.join(script_dir, 'convert.mjs')
            result = subprocess.run(
                ['node', convert_script, 'convert', temp_file],
                capture_output=True,
                text=True,
                encoding='utf-8'
            )
            
            if result.returncode != 0:
                print(f"  ✗ 转换失败: {result.stderr}")
                return None
            
            # 解析输出为JSON
            singbox_config = json.loads(result.stdout)
            return singbox_config
            
        finally:
            # 清理临时文件
            os.unlink(temp_file)
            
    except Exception as e:
        print(f"  ✗ 转换异常: {e}")
        return None


def merge_singbox_config(singbox_nodes_list, script_dir):
    """
    将多个sing-box订阅节点合并到配置模板
    
    Args:
        singbox_nodes_list: sing-box格式的代理节点列表（来自多个订阅的合并）
        script_dir: 脚本所在目录
    
    Returns:
        合并后的完整配置JSON，或None表示失败
    """
    try:
        template_path = os.path.join(script_dir, '..', 'sing-box_template.jsonc')
        if not os.path.exists(template_path):
            print(f"  ✗ 配置模板不存在: {template_path}")
            return None
        
        # 创建临时文件存储订阅节点
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(singbox_nodes_list, f)
            sub_temp_file = f.name
        
        try:
            # 调用Python合并脚本
            merge_script = os.path.join(script_dir, 'merge_config.py')
            result = subprocess.run(
                ['python', merge_script, template_path, sub_temp_file],
                capture_output=True,
                text=True,
                encoding='utf-8'
            )
            
            if result.returncode != 0:
                print(f"  ✗ 合并失败: {result.stderr}")
                return None
            
            # 检查stdout是否为空或只有空白
            stdout = result.stdout.strip()
            if not stdout:
                print(f"  ✗ 合并脚本没有输出")
                return None
            
            # 解析输出为JSON
            merged_config = json.loads(stdout)
            return merged_config
            
        finally:
            # 清理临时文件
            os.unlink(sub_temp_file)
            
    except Exception as e:
        print(f"  ✗ 合并异常: {e}")
        return None


def get_subs_for_singbox_config(subscription_names, singbox_subs_setting):
    """
    根据设置获取用于生成sing-box配置的订阅列表
    
    Args:
        subscription_names: 所有订阅名称列表
        singbox_subs_setting: 环境变量 SINGBOX_CONFIG_SUBS 的值
    
    Returns:
        选中的订阅名称列表
    """
    if not singbox_subs_setting or singbox_subs_setting.lower() == 'all':
        return subscription_names
    
    # 解析逗号分隔的订阅名称
    selected = [s.strip() for s in singbox_subs_setting.split(',') if s.strip()]
    
    # 过滤出存在的订阅
    valid_subs = [s for s in selected if s in subscription_names]
    
    if not valid_subs:
        print(f"  ⚠️ 没有找到匹配的订阅，使用全部订阅")
        return subscription_names
    
    return valid_subs


def main():
    print("=" * 60)
    print("SubDl - Subscription Downloader")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    github_token = get_env_var("GH_TOKEN", required=True)
    gist_id = get_env_var("GIST_ID", default="")
    user_agent = get_env_var("USER_AGENT", default="clash-verge/v2.4.4")
    enable_convert = get_env_var("ENABLE_SINGBOX_CONVERT", default="true").lower() == "true"
    singbox_subs_setting = get_env_var("SINGBOX_CONFIG_SUBS", default="all")
    
    # 获取脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    subscriptions = parse_subscriptions()
    if not subscriptions:
        print("错误: 未找到订阅配置")
        sys.exit(1)
    
    print(f"\n找到 {len(subscriptions)} 个订阅")
    if enable_convert:
        print("Sing-box转换: 已启用")
        print(f"用于生成配置的订阅: {singbox_subs_setting}")
    else:
        print("Sing-box转换: 已禁用")
    print()
    
    files = {}
    subscription_info = []
    failed = []
    
    # 存储所有sing-box节点（用于生成最终配置）
    all_singbox_nodes = []
    # 记录哪些订阅需要包含在sing-box配置中
    all_subscription_names = [sub['name'] for sub in subscriptions]
    
    for sub in subscriptions:
        print(f"下载: {sub['name']}")
        try:
            content, flow_info = download_subscription(sub["url"], user_agent)
            files[sub["filename"]] = content
            subscription_info.append({
                "name": sub["name"],
                "flow": flow_info
            })
            print(f"  ✓ 成功 ({len(content)} 字节)")
            
            # 转换为Sing-box格式
            if enable_convert:
                print(f"  → 转换为Sing-box格式...")
                singbox_config = convert_to_singbox(content, script_dir)
                if singbox_config:
                    # 获取节点列表（可能是完整配置或直接是节点数组）
                    singbox_nodes = singbox_config if isinstance(singbox_config, list) else singbox_config.get('outbounds', [])
                    
                    # 保存原始sing-box订阅（不含模板）
                    singbox_filename = f"{sub['name']}-singbox.json"
                    files[singbox_filename] = json.dumps(singbox_config, indent=2, ensure_ascii=False)
                    print(f"  ✓ 转换成功 ({len(files[singbox_filename])} 字节, {len(singbox_nodes)} 个节点)")
                    
                    # 收集节点用于最终配置合并
                    all_singbox_nodes.extend(singbox_nodes)
        except Exception as e:
            print(f"  ✗ 失败: {e}")
            failed.append({"name": sub["name"], "error": str(e)})
    
    if not files:
        print("错误: 所有订阅下载失败")
        sys.exit(1)
    
    # 根据设置筛选用于生成sing-box配置的订阅
    selected_subs = get_subs_for_singbox_config(all_subscription_names, singbox_subs_setting)
    print(f"  → 选定的订阅: {selected_subs}")
    print(f"  → 所有订阅: {all_subscription_names}")
    
    # 生成最终的sing-box配置
    if enable_convert and all_singbox_nodes:
        print(f"\n→ 合并 {len(selected_subs)} 个订阅的节点到配置模板...")
        print(f"  → 当前条件: len(selected)={len(selected_subs)} != len(all)={len(all_subscription_names)} => {len(selected_subs) != len(all_subscription_names)}")
        print(f"  → 当前条件: selected != all => {selected_subs != all_subscription_names}")
        
        # 如果不是全部订阅，需要重新筛选节点
        if len(selected_subs) != len(all_subscription_names) or selected_subs != all_subscription_names:
            print(f"  → 重新筛选节点（当前: {len(all_singbox_nodes)} 个）")
            # 重新下载并转换选定的订阅
            filtered_nodes = []
            for sub in subscriptions:
                if sub['name'] in selected_subs:
                    print(f"  → 重新处理: {sub['name']}")
                    try:
                        content, _ = download_subscription(sub["url"], user_agent)
                        singbox_config = convert_to_singbox(content, script_dir)
                        if singbox_config:
                            nodes = singbox_config if isinstance(singbox_config, list) else singbox_config.get('outbounds', [])
                            filtered_nodes.extend(nodes)
                            print(f"    → 获取 {len(nodes)} 个节点")
                    except Exception as e:
                        print(f"    ✗ {sub['name']} 转换失败: {e}")
            final_nodes = filtered_nodes
        else:
            final_nodes = all_singbox_nodes
            print(f"  → 使用已收集的节点: {len(final_nodes)} 个")
        
        if final_nodes:
            merged_config = merge_singbox_config(final_nodes, script_dir)
            if merged_config:
                files["sing-box-config.json"] = json.dumps(merged_config, indent=2, ensure_ascii=False)
                print(f"  ✓ 合并成功 ({len(files['sing-box-config.json'])} 字节, {len(final_nodes)} 个节点)")
    
    # 添加时间戳
    files[".last_update"] = f"Last updated: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S CST')}\n"
    
    # 生成并保存 README
    readme_content = generate_readme(subscription_info)
    with open("README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)
    print("\n✓ README 已更新")
    
    # 上传 Gist
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
