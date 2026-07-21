#!/usr/bin/env node
/**
 * Clash → Sing-box 批量转换脚本
 * 由 Python subprocess.run 启动，进程退出时自动清理全部状态。
 *
 * 用法: node convert.mjs batch-convert <input-file>
 *   stdin/stdout: JSON
 *   input:  { "sub_name": "clash_content", ... }
 *   output: { "sub_name": singbox_dict | null, ... }
 */

import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import { JSDOM } from 'jsdom';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROXY_UTILS_FILE = path.join(__dirname, 'deps', 'proxy-utils.esm.mjs');

/**
 * 批量转换：一次加载模块，转换多个订阅内容
 */
async function batchConvertToSingbox(batchInput) {
    // 注入 require（Sub-Store 内部隐式依赖 dotenv）
    const { createRequire } = await import('module');
    global.require = createRequire(PROXY_UTILS_FILE);

    // 创建 jsdom 环境
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        url: 'https://localhost',
        pretendToBeVisual: true,
    });
    global.window = dom.window;
    global.document = dom.window.document;
    global.self = dom.window;
    Object.defineProperty(global, 'navigator', {
        value: dom.window,
        writable: true,
        configurable: true,
    });
    global.location = dom.window.location;

    const { parse, produce } = await import('file://' + PROXY_UTILS_FILE);
    console.error(`[Convert] 模块加载成功，开始转换 ${Object.keys(batchInput).length} 个订阅`);

    const results = {};
    for (const [name, clashContent] of Object.entries(batchInput)) {
        try {
            results[name] = JSON.parse(produce(parse(clashContent), 'singbox'));
            console.error(`[Convert]   ✓ ${name}`);
        } catch (err) {
            console.error(`[Convert]   ✗ ${name}: ${err.message}`);
            results[name] = null;
        }
    }
    return results;
}

async function main() {
    const args = process.argv.slice(2);
    if (args[0] !== 'batch-convert' || !args[1]) {
        console.error('用法: node convert.mjs batch-convert <input-file>');
        process.exit(1);
    }

    // 所有日志输出到 stderr，stdout 只输出 JSON 结果
    const originalLog = console.log;
    console.log = (...a) => console.error(...a);

    try {
        const batchInput = JSON.parse(await fs.readFile(args[1], 'utf8'));
        const results = await batchConvertToSingbox(batchInput);

        // 恢复 console.log，只输出 JSON 到 stdout
        console.log = originalLog;
        console.log(JSON.stringify(results));
    } catch (err) {
        console.error(`[Convert] 错误: ${err.message}`);
        console.error(err.stack);
        process.exit(1);
    }
}

main();