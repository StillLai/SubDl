#!/usr/bin/env node
/**
 * Clash to Sing-box 转换脚本
 * 使用 Sub-Store 的 sub-store.bundle.js (CJS格式)
 */

const fs = require('fs');
const path = require('path');
const https = require('https');
const { execSync } = require('child_process');

const DEPS_DIR = path.join(__dirname, 'deps');
const SUBSTORE_BUNDLE = path.join(DEPS_DIR, 'sub-store.bundle.js');
const VERSION_FILE = path.join(DEPS_DIR, '.version');

const RELEASES_API = 'https://api.github.com/repos/sub-store-org/Sub-Store/releases/latest';
const BUNDLE_NAME = 'sub-store.bundle.js';

/**
 * 下载文件
 */
function downloadFile(url, dest, headers = {}) {
    return new Promise((resolve, reject) => {
        const file = fs.createWriteStream(dest);
        https.get(url, { headers }, (response) => {
            if (response.statusCode === 302 || response.statusCode === 301) {
                file.close();
                fs.unlinkSync(dest);
                downloadFile(response.headers.location, dest, headers).then(resolve).catch(reject);
                return;
            }
            if (response.statusCode !== 200) {
                file.close();
                fs.unlinkSync(dest);
                reject(new Error(`下载失败: ${response.statusCode}`));
                return;
            }
            response.pipe(file);
            file.on('finish', () => file.close(resolve));
        }).on('error', (err) => {
            if (fs.existsSync(dest)) fs.unlinkSync(dest);
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
        if (githubToken) headers['Authorization'] = `token ${githubToken}`;

        https.get(RELEASES_API, { headers }, (response) => {
            let data = '';
            if (response.statusCode === 403) {
                reject(new Error('API限流'));
                return;
            }
            if (response.statusCode !== 200) {
                reject(new Error(`API请求失败: ${response.statusCode}`));
                return;
            }
            response.on('data', (chunk) => data += chunk);
            response.on('end', () => {
                try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
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
        
        let currentVersion = fs.existsSync(VERSION_FILE) ? fs.readFileSync(VERSION_FILE, 'utf8').trim() : '';

        if (currentVersion === tagName && fs.existsSync(SUBSTORE_BUNDLE)) {
            console.log(`[Convert] 已是最新版本: ${tagName}`);
            return;
        }

        console.log(`[Convert] 发现新版本: ${tagName}`);

        const asset = release.assets.find(a => 
            a.uploader?.login === 'github-actions[bot]' && 
            a.name === BUNDLE_NAME
        );

        if (!asset) {
            if (!fs.existsSync(SUBSTORE_BUNDLE)) throw new Error('未找到依赖文件');
            console.log('[Convert] 使用本地缓存版本');
            return;
        }

        console.log('[Convert] 下载依赖...');
        await downloadFile(asset.browser_download_url, SUBSTORE_BUNDLE + '.tmp');
        if (fs.existsSync(SUBSTORE_BUNDLE)) fs.unlinkSync(SUBSTORE_BUNDLE);
        fs.renameSync(SUBSTORE_BUNDLE + '.tmp', SUBSTORE_BUNDLE);
        fs.writeFileSync(VERSION_FILE, tagName);
        console.log('[Convert] 依赖更新成功');

    } catch (err) {
        console.error('[Convert] 检查更新失败:', err.message);
        if (!fs.existsSync(SUBSTORE_BUNDLE)) throw new Error('依赖检查失败且本地无缓存');
        console.log('[Convert] 使用本地缓存版本');
    }
}

/**
 * 从bundle中提取ProxyUtils
 */
function loadProxyUtils() {
    // sub-store.bundle.js 是一个完整的后端bundle
    // 我们需要从中提取parse和produce函数
    
    // 清空require缓存
    delete require.cache[require.resolve(SUBSTORE_BUNDLE)];
    
    // 加载bundle
    const SubStore = require(SUBSTORE_BUNDLE);
    
    // 尝试找到ProxyUtils
    // 根据Sub-Store源码，ProxyUtils在全局或导出中
    if (SubStore.ProxyUtils) {
        return SubStore.ProxyUtils;
    }
    
    // 尝试从global获取
    if (global.ProxyUtils) {
        return global.ProxyUtils;
    }
    
    // 如果找不到，说明bundle结构不同，需要检查
    console.log('[Convert] Bundle导出:', Object.keys(SubStore));
    throw new Error('无法在bundle中找到ProxyUtils');
}

/**
 * 转换Clash配置到Sing-box格式
 */
function convertClashToSingbox(clashContent) {
    const { parse, produce } = loadProxyUtils();
    const proxies = parse(clashContent);
    return produce(proxies, 'singbox', 'internal');
}

/**
 * 主函数
 */
async function main() {
    const args = process.argv.slice(2);
    const command = args[0];
    const githubToken = process.env.GH_TOKEN || '';

    try {
        if (!fs.existsSync(DEPS_DIR)) fs.mkdirSync(DEPS_DIR, { recursive: true });
        await checkAndUpdateDeps(githubToken);

        if (command === 'convert') {
            const inputFile = args[1];
            if (!inputFile) {
                console.error('用法: node convert.cjs convert <input-file>');
                process.exit(1);
            }

            const clashContent = fs.readFileSync(inputFile, 'utf8');
            const singboxConfig = convertClashToSingbox(clashContent);
            console.log(JSON.stringify(singboxConfig, null, 2));
        } else {
            console.log('用法: node convert.cjs convert <input-file>');
            process.exit(1);
        }
    } catch (err) {
        console.error('[Convert] 错误:', err.message);
        console.error(err.stack);
        process.exit(1);
    }
}

main();