#!/usr/bin/env node
/**
 * Clash to Sing-box 转换脚本
 * 使用 Sub-Store 的 proxy-utils.esm.mjs 作为依赖
 */

import fs from 'fs';
import path from 'path';
import https from 'https';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const DEPS_DIR = path.join(__dirname, 'deps');
const PROXY_UTILS_FILE = path.join(DEPS_DIR, 'proxy-utils.esm.mjs');
const VERSION_FILE = path.join(DEPS_DIR, '.version');

// GitHub Releases API
const RELEASES_API = 'https://api.github.com/repos/sub-store-org/Sub-Store/releases/latest';
const PROXY_UTILS_NAME = 'proxy-utils.esm.mjs';

/**
 * 下载文件
 */
function downloadFile(url, dest, headers = {}) {
    return new Promise((resolve, reject) => {
        const file = fs.createWriteStream(dest);
        https.get(url, { headers }, (response) => {
            if (response.statusCode === 302 || response.statusCode === 301) {
                // 重定向
                file.close();
                if (fs.existsSync(dest)) fs.unlinkSync(dest);
                downloadFile(response.headers.location, dest, headers).then(resolve).catch(reject);
                return;
            }
            if (response.statusCode !== 200) {
                file.close();
                if (fs.existsSync(dest)) fs.unlinkSync(dest);
                reject(new Error(`下载失败: ${response.statusCode}`));
                return;
            }
            response.pipe(file);
            file.on('finish', () => {
                file.close(resolve);
            });
        }).on('error', (err) => {
            if (fs.existsSync(dest)) {
                try { fs.unlinkSync(dest); } catch {}
            }
            reject(err);
        });
    });
}

/**
 * 获取GitHub Releases信息
 */
function getLatestRelease(githubToken) {
    return new Promise((resolve, reject) => {
        const headers = {
            'User-Agent': 'SubDl-Converter',
            'Accept': 'application/vnd.github.v3+json'
        };
        if (githubToken) {
            headers['Authorization'] = `token ${githubToken}`;
        }

        https.get(RELEASES_API, { headers }, (response) => {
            let data = '';
            
            if (response.statusCode === 403) {
                reject(new Error('API限流，请稍后再试或使用GH_TOKEN'));
                return;
            }
            
            if (response.statusCode !== 200) {
                reject(new Error(`API请求失败: ${response.statusCode}`));
                return;
            }

            response.on('data', (chunk) => {
                data += chunk;
            });

            response.on('end', () => {
                try {
                    const release = JSON.parse(data);
                    resolve(release);
                } catch (e) {
                    reject(e);
                }
            });
        }).on('error', reject);
    });
}

/**
 * 检查并更新依赖
 */
async function checkAndUpdateDeps(githubToken) {
    try {
        console.log('[Convert] 检查 Sub-Store 依赖更新...');
        const release = await getLatestRelease(githubToken);
        const tagName = release.tag_name;
        
        // 检查本地版本
        let currentVersion = '';
        if (fs.existsSync(VERSION_FILE)) {
            currentVersion = fs.readFileSync(VERSION_FILE, 'utf8').trim();
        }

        if (currentVersion === tagName && fs.existsSync(PROXY_UTILS_FILE)) {
            console.log(`[Convert] 已是最新版本: ${tagName}`);
            return;
        }

        console.log(`[Convert] 发现新版本: ${tagName} (当前: ${currentVersion || '无'})`);

        // 查找proxy-utils.esm.mjs资源
        const asset = release.assets.find(a => 
            a.uploader?.login === 'github-actions[bot]' && 
            a.name === PROXY_UTILS_NAME
        );

        if (!asset) {
            console.error('[Convert] 未找到依赖文件:', PROXY_UTILS_NAME);
            if (!fs.existsSync(PROXY_UTILS_FILE)) {
                throw new Error('本地无缓存且无法下载依赖');
            }
            console.log('[Convert] 使用本地缓存版本');
            return;
        }

        // 下载文件
        console.log('[Convert] 下载依赖:', asset.browser_download_url);
        await downloadFile(asset.browser_download_url, PROXY_UTILS_FILE + '.tmp');
        
        // 替换旧文件
        if (fs.existsSync(PROXY_UTILS_FILE)) {
            fs.unlinkSync(PROXY_UTILS_FILE);
        }
        fs.renameSync(PROXY_UTILS_FILE + '.tmp', PROXY_UTILS_FILE);
        
        // 保存版本信息
        fs.writeFileSync(VERSION_FILE, tagName);
        console.log('[Convert] 依赖更新成功');

    } catch (err) {
        console.error('[Convert] 检查更新失败:', err.message);
        if (!fs.existsSync(PROXY_UTILS_FILE)) {
            throw new Error('依赖检查失败且本地无缓存');
        }
        console.log('[Convert] 使用本地缓存版本');
    }
}

/**
 * 转换Clash配置到Sing-box格式
 */
async function convertClashToSingbox(clashContent) {
    // 动态导入proxy-utils.esm.mjs（ES模块）
    const modulePath = 'file://' + PROXY_UTILS_FILE;
    const { parse, produce } = await import(modulePath);
    
    // 解析clash内容
    const proxies = parse(clashContent);
    
    // 转换为sing-box格式
    const singboxProxies = produce(proxies, 'singbox', 'internal');
    
    return singboxProxies;
}

/**
 * 主函数
 */
async function main() {
    const args = process.argv.slice(2);
    const command = args[0];
    const githubToken = process.env.GH_TOKEN || '';

    try {
        // 确保依赖目录存在
        if (!fs.existsSync(DEPS_DIR)) {
            fs.mkdirSync(DEPS_DIR, { recursive: true });
        }

        // 检查并更新依赖
        await checkAndUpdateDeps(githubToken);

        if (command === 'convert') {
            const inputFile = args[1];
            if (!inputFile) {
                console.error('用法: node convert.mjs convert <input-file>');
                process.exit(1);
            }

            const clashContent = fs.readFileSync(inputFile, 'utf8');
            const singboxConfig = await convertClashToSingbox(clashContent);
            
            console.log(JSON.stringify(singboxConfig, null, 2));
        } else {
            console.log('用法:');
            console.log('  node convert.mjs convert <input-file>  转换Clash配置到Sing-box');
            process.exit(1);
        }
    } catch (err) {
        console.error('[Convert] 错误:', err.message);
        console.error(err.stack);
        process.exit(1);
    }
}

main();