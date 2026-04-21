#!/usr/bin/env python3
"""
Subscription Downloader and Gist Uploader

定期拉取 Clash 订阅并上传到 GitHub Gists。
"""

import os
import sys
import json
import base64
import re
import time
from urllib.parse import urlparse
from datetime import datetime

import requests


def get_env_var(name, default=None, required=False):
    """获取环境变量"""
    value = os.environ.get(name, default)
    if required and not value:
        raise ValueError(f"环境变量 {name} 未设置")
    return value


def download_subscription(url, user_agent, timeout=30000):
    """下载订阅内容"""
    print(f"正在下载订阅: {url[:60]}...")
    
    headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout / 1000,  # 转换为秒
            allow_redirects=True
        )
        response.raise_for_status()
        
        content = response.text
        
        # 检查内容是否为空
        if not content or len(content.strip()) == 0:
            raise ValueError("订阅内容为空")
        
        # 检查内容是否有效（至少包含一些代理节点）
        content_stripped = content.replace(" ", "").replace("\n", "").replace("\r", "")
        if len(content_stripped) == 0:
            raise ValueError("订阅内容为空")
        
        print(f"  ✓ 下载成功 ({len(content)} 字节)")
        return content
        
    except requests.exceptions.Timeout:
        raise TimeoutError(f"下载超时: {url}")
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"下载失败: {e}")


def parse_subscriptions(env_value):
    """解析订阅配置"""
    if not env_value:
        return []
    
    subscriptions = []
    
    # 尝试解析为 JSON
    try:
        data = json.loads(env_value)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    subscriptions.append({
                        "name": item.get("name", "subscription"),
                        "url": item.get("url"),
                        "filename": item.get("filename", item.get("name", "subscription") + ".yaml")
                    })
                elif isinstance(item, str):
                    # 简单的 URL 字符串
                    name = extract_name_from_url(item)
                    subscriptions.append({
                        "name": name,
                        "url": item,
                        "filename": f"{name}.yaml"
                    })
        elif isinstance(data, dict):
            # 单个订阅对象
            subscriptions.append({
                "name": data.get("name", "subscription"),
                "url": data.get("url"),
                "filename": data.get("filename", data.get("name", "subscription") + ".yaml")
            })
        return subscriptions
    except json.JSONDecodeError:
        pass
    
    # 尝试按行分割（每行一个 URL）
    lines = env_value.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # 检查是否包含分隔符（name|url 或 url|filename 格式）
        if "|" in line:
            parts = line.split("|")
            if len(parts) >= 2:
                name = parts[0].strip()
                url = parts[1].strip()
                filename = parts[2].strip() if len(parts) >= 3 else f"{name}.yaml"
                subscriptions.append({
                    "name": name,
                    "url": url,
                    "filename": filename
                })
        else:
            # 简单的 URL
            name = extract_name_from_url(line)
            subscriptions.append({
                "name": name,
                "url": line,
                "filename": f"{name}.yaml"
            })
    
    return subscriptions


def extract_name_from_url(url):
    """从 URL 提取名称"""
    try:
        parsed = urlparse(url)
        # 使用域名作为名称
        domain = parsed.netloc.replace("www.", "").split(":")[0]
        # 移除特殊字符
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', domain)
        return name[:50]  # 限制长度
    except:
        # 使用时间戳作为备用
        return f"sub_{int(time.time())}"


def upload_to_gist(github_token, gist_id, files, description="SubDl Subscriptions"):
    """上传文件到 Gist"""
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "SubDl/1.0"
    }
    
    # 准备文件数据
    gist_files = {}
    for filename, content in files.items():
        gist_files[filename] = {"content": content}
    
    # 如果没有指定 gist_id，创建新的 gist
    if not gist_id:
        print("创建新的 Gist...")
        url = "https://api.github.com/gists"
        data = {
            "description": description,
            "public": False,  # 私密 gist
            "files": gist_files
        }
        
        response = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        gist_id = result["id"]
        print(f"  ✓ Gist 创建成功: {gist_id}")
        print(f"  ✓ Gist URL: {result['html_url']}")
        return gist_id
    
    # 更新现有 gist
    print(f"更新 Gist: {gist_id}...")
    url = f"https://api.github.com/gists/{gist_id}"
    
    # 首先获取现有的 gist 信息
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code == 404:
        print("  ! 指定的 Gist ID 不存在，将创建新的 Gist")
        return upload_to_gist(github_token, None, files, description)
    
    response.raise_for_status()
    existing_gist = response.json()
    
    # 构建更新数据
    data = {"files": gist_files}
    
    response = requests.patch(
        url,
        headers=headers,
        json=data,
        timeout=30
    )
    response.raise_for_status()
    
    print(f"  ✓ Gist 更新成功")
    return gist_id


def main():
    """主函数"""
    print("=" * 60)
    print("SubDl - Subscription Downloader")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    try:
        # 获取配置
        github_token = get_env_var("GITHUB_TOKEN", required=True)
        gist_id = get_env_var("GIST_ID", default="")
        user_agent = get_env_var("USER_AGENT", default="clash-verge/v2.4.4")
        sub_urls_env = get_env_var("SUBSCRIPTION_URLS", required=True)
        
        # 解析订阅配置
        subscriptions = parse_subscriptions(sub_urls_env)
        
        if not subscriptions:
            print("错误: 未找到有效的订阅配置")
            sys.exit(1)
        
        print(f"\n找到 {len(subscriptions)} 个订阅")
        print("-" * 60)
        
        # 下载所有订阅
        files = {}
        failed = []
        
        for sub in subscriptions:
            name = sub["name"]
            url = sub["url"]
            filename = sub["filename"]
            
            if not url:
                print(f"跳过 {name}: URL 为空")
                continue
            
            try:
                content = download_subscription(url, user_agent)
                files[filename] = content
            except Exception as e:
                print(f"  ✗ 下载失败: {e}")
                failed.append({"name": name, "url": url, "error": str(e)})
        
        print("-" * 60)
        
        if not files:
            print("错误: 所有订阅下载失败")
            sys.exit(1)
        
        # 添加上传时间戳文件
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        files[".last_update"] = f"Last updated: {timestamp}\n"
        
        # 如果存在失败记录，添加错误日志
        if failed:
            error_log = f"Failed subscriptions ({len(failed)}):\n\n"
            for item in failed:
                error_log += f"- {item['name']}: {item['error']}\n"
            files[".errors"] = error_log
        
        # 上传到 Gist
        print(f"\n上传 {len(files)} 个文件到 Gist...")
        new_gist_id = upload_to_gist(github_token, gist_id, files)
        
        # 如果创建了新的 gist，提示用户更新配置
        if new_gist_id != gist_id:
            print(f"\n" + "=" * 60)
            print("重要提示: 已创建新的 Gist")
            print(f"请在 Repository secrets 中设置 GIST_ID = {new_gist_id}")
            print("=" * 60)
        
        print(f"\n完成! 成功处理 {len(files) - 1 - (1 if failed else 0)} 个订阅")
        print(f"更新时间: {timestamp}")
        
        # 设置输出变量（供后续步骤使用）
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"gist_id={new_gist_id}\n")
                f.write(f"success_count={len(files) - 1 - (1 if failed else 0)}\n")
        
        # 如果有失败，以非零退出码退出
        if failed:
            print(f"\n警告: {len(failed)} 个订阅下载失败")
            sys.exit(2)  # 部分成功
            
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()