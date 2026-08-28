// 验证主题注入链路：从 index.html 提取真实的 __fhToCssValue/__fhApplyTokens，
// 用内置主题文件的 token 跑一遍，确认 hex -> "R G B" 分量转换正确。
const fs = require('fs');
const path = require('path');

// 脚本位于 python/tests/ 下，据此推断静态资源目录，避免硬编码绝对路径
const STATIC = path.resolve(__dirname, '..', 'finhack_pro', 'webui', 'static');
const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');

// --- 提取真实实现（不重新实现，避免测试与实现脱节）---
const start = html.indexOf('window.__fhToCssValue = function');
const end = html.indexOf('window.__fhApplyTokens', start);
if (start < 0 || end < 0) {
    console.error('提取失败：index.html 中未找到 __fhToCssValue 定义');
    process.exit(1);
}
const toCssValueSrc = html.slice(start, end);

const aStart = html.indexOf('window.__fhApplyTokens = function');
const aEnd = html.indexOf('(function () {', aStart);
const applyTokensSrc = html.slice(aStart, aEnd);

global.window = { __fhToCssValue: null, __fhApplyTokens: null };
global.document = {
    documentElement: {
        style: {
            _props: {},
            setProperty(k, v) { this._props[k] = v; }
        }
    }
};
eval(toCssValueSrc);
eval(applyTokensSrc);

const toCss = window.__fhToCssValue;
const applyTokens = window.__fhApplyTokens;
const props = document.documentElement.style._props;

// --- 1) 转换函数单测 ---
const cases = [
    ['#0B0C0E', '11 12 14'],
    ['#FFFFFF', '255 255 255'],
    ['#abc', '170 187 204'],
    ['#ABC', '170 187 204'],
    ['  #17181A  ', '23 24 26'],
    ['rgba(232,234,237,.10)', 'rgba(232,234,237,.10)'],
    ['rgba(52,56,63,.85)', 'rgba(52,56,63,.85)'],
];
let fail = 0;
console.log('=== 1) hex -> 分量转换 ===');
for (const [input, expected] of cases) {
    const got = toCss(input);
    const ok = got === expected;
    if (!ok) fail++;
    console.log(`  ${ok ? 'OK ' : '!! '} ${JSON.stringify(input).padEnd(26)} -> ${JSON.stringify(got)}${ok ? '' : ' (期望 ' + JSON.stringify(expected) + ')'}`);
}

// --- 2) 用真实主题文件跑注入 ---
console.log('\n=== 2) 内置主题文件注入 ===');
for (const name of ['mono-dark.json', 'mono-light.json']) {
    const theme = JSON.parse(fs.readFileSync(path.join(STATIC, 'themes', name), 'utf8'));
    for (const k in props) delete props[k];
    applyTokens(theme.tokens);

    const tokens = theme.tokens;
    const injected = Object.keys(props);
    const missing = Object.keys(tokens).filter(k => !(('--' + k) in props));
    const bad = Object.keys(tokens).filter(k => {
        const v = props['--' + k];
        return tokens[k].startsWith('#') && !/^\d{1,3} \d{1,3} \d{1,3}$/.test(v);
    });

    const ok = missing.length === 0 && bad.length === 0 && injected.length === Object.keys(tokens).length;
    if (!ok) fail++;
    console.log(`  ${ok ? 'OK ' : '!! '} ${theme.id}: ${injected.length} 个变量注入` +
        (missing.length ? ` | 缺失 ${missing}` : '') + (bad.length ? ` | 转换异常 ${bad}` : ''));
    console.log(`      示例 --bg-base="${props['--bg-base']}"  --up="${props['--up']}"  --down="${props['--down']}"`);
}

// --- 3) 涨跌方向（cn 应为红涨绿跌）---
console.log('\n=== 3) 涨跌方向校验（cn: 涨红跌绿）===');
for (const name of ['mono-dark.json', 'mono-light.json']) {
    const theme = JSON.parse(fs.readFileSync(path.join(STATIC, 'themes', name), 'utf8'));
    for (const k in props) delete props[k];
    applyTokens(theme.tokens);
    const parse = s => s.split(' ').map(Number);
    const [ur, ug] = parse(props['--up']);
    const [dr, dg] = parse(props['--down']);
    const upIsRed = ur > ug;
    const downIsGreen = dg > dr;
    const ok = upIsRed && downIsGreen;
    if (!ok) fail++;
    console.log(`  ${ok ? 'OK ' : '!! '} ${theme.id}: up=rgb(${props['--up']}) 偏${upIsRed ? '红' : '绿'} | down=rgb(${props['--down']}) 偏${downIsGreen ? '绿' : '红'}`);
}

console.log(`\n结果: ${fail === 0 ? '全部通过' : fail + ' 项失败'}`);
process.exit(fail === 0 ? 0 : 1);
