#!/usr/bin/env node
/**
 * Clash to Sing-box 转换脚本
 * 使用 Sub-Store 的 proxy-utils.esm.mjs 作为依赖
 * 使用jsdom模拟浏览器环境
 */

import fs from 'fs';
import path from 'path';
import https from 'https';
import { fileURLToPath } from 'url';
import { JSDOM } from 'jsdom';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const DEPS_DIR = path.join(__dirname, 'deps');
const PROXY_UTILS_FILE = path.join(DEPS_DIR, 'proxy-utils.esm.mjs');
const VERSION_FILE = path.join(DEPS_DIR, '.version');

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

        if (currentVersion === tagName && fs.existsSync(PROXY_UTILS_FILE)) {
            console.log(`[Convert] 已是最新版本: ${tagName}`);
            return;
        }

        console.log(`[Convert] 发现新版本: ${tagName}`);

        const asset = release.assets.find(a => 
            a.uploader?.login === 'github-actions[bot]' && 
            a.name === PROXY_UTILS_NAME
        );

        if (!asset) {
            if (!fs.existsSync(PROXY_UTILS_FILE)) throw new Error('未找到依赖文件');
            console.log('[Convert] 使用本地缓存版本');
            return;
        }

        console.log('[Convert] 下载依赖...');
        await downloadFile(asset.browser_download_url, PROXY_UTILS_FILE + '.tmp');
        if (fs.existsSync(PROXY_UTILS_FILE)) fs.unlinkSync(PROXY_UTILS_FILE);
        fs.renameSync(PROXY_UTILS_FILE + '.tmp', PROXY_UTILS_FILE);
        fs.writeFileSync(VERSION_FILE, tagName);
        console.log('[Convert] 依赖更新成功');

    } catch (err) {
        console.error('[Convert] 检查更新失败:', err.message);
        if (!fs.existsSync(PROXY_UTILS_FILE)) throw new Error('依赖检查失败且本地无缓存');
        console.log('[Convert] 使用本地缓存版本');
    }
}

/**
 * 使用jsdom创建浏览器环境并加载模块
 */
async function loadProxyUtils() {
    // 创建jsdom实例
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        url: 'https://localhost',
        pretendToBeVisual: true,
        resources: 'usable'
    });
    
    const { window } = dom;
    
    // 将浏览器API暴露到global，让ES模块可以使用
    global.window = window;
    global.document = window.document;
    global.Blob = window.Blob;
    global.URL = window.URL;
    global.URLSearchParams = window.URLSearchParams;
    global.TextEncoder = window.TextEncoder;
    global.TextDecoder = window.TextDecoder;
    global.fetch = window.fetch;
    global.navigator = window.navigator;
    global.location = window.location;
    global.self = window;
    global.top = window;
    global.parent = window;
    
    // 读取ESM文件内容
    const source = fs.readFileSync(PROXY_UTILS_FILE, 'utf8');
    
    // 创建一个Blob URL来加载模块（模拟浏览器行为）
    const blob = new window.Blob([source], { type: 'text/javascript' });
    const blobUrl = window.URL.createObjectURL(blob);
    
    try {
        // 使用动态import加载Blob URL
        const mod = await import(blobUrl);
        console.log('[Convert] 模块加载成功');
        
        // 清理
        window.URL.revokeObjectURL(blobUrl);
        
        return mod;
    } catch (e) {
        window.URL.revokeObjectURL(blobUrl);
        throw new Error(`加载模块失败: ${e.message}`);
    }
}

/**
 * 转换Clash配置到Sing-box格式
 */
async function convertClashToSingbox(clashContent) {
    const { parse, produce } = await loadProxyUtils();
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
                console.error('用法: node convert.mjs convert <input-file>');
                process.exit(1);
            }

            const clashContent = fs.readFileSync(inputFile, 'utf8');
            const singboxConfig = await convertClashToSingbox(clashContent);
            console.log(JSON.stringify(singboxConfig, null, 2));
        } else {
            console.log('用法: node convert.mjs convert <input-file>');
            process.exit(1);
        }
    } catch (err) {
        console.error('[Convert] 错误:', err.message);
        console.error(err.stack);
        process.exit(1);
    }
}

main();