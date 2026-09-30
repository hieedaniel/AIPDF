#!/usr/bin/env node
/**
 * scripts/check-miniapp.js —— 小程序页面的静态自检（零依赖，node 运行）
 *
 * 改完 wxml / wxss / js 后跑一次，能挡住 90% 的低级错误：
 *   1. wxml 绑定的事件处理函数在 js 里是否存在
 *   2. wxml 用到的 data 字段是否在 data 里声明、setData 的 key 是否都声明过
 *   3. wxml 用到的 class 是否在 wxss 里有定义（漏写样式）
 *   4. wxss 定义了但 wxml 没用的 class（冗余样式）
 *
 * 用法：
 *   node scripts/check-miniapp.js                 # 默认检查 pages/index/index
 *   node scripts/check-miniapp.js miniapp/pages/index/index
 * 退出码 = 错误数（0 表示通过）。警告不影响退出码。
 */
'use strict';

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const target = process.argv[2] || 'miniapp/pages/index/index';
const abs = (p) => path.resolve(root, p);

const read = (p) => {
  if (!fs.existsSync(p)) return null;
  return fs.readFileSync(p, 'utf8').replace(/^\uFEFF/, '');
};

const wxml = read(abs(`${target}.wxml`));
const js = read(abs(`${target}.js`));
const wxss = read(abs(`${target}.wxss`));
const appWxss = read(abs('miniapp/app.wxss'));

if (!wxml || !js) {
  console.error(`[x] 找不到 ${target}.wxml 或 .js`);
  process.exit(1);
}

const errors = [];
const warnings = [];
const infos = [];

/* ---------------------------------------------------------------- 事件绑定 */
const handlers = new Set();
for (const m of wxml.matchAll(/\b(?:bind|catch|capture-bind|capture-catch)[:\w-]*\s*=\s*"([^"{}]+)"/g)) {
  const name = m[1].trim();
  if (name) handlers.add(name);
}

/* ------------------------------------------------------------------ 方法表 */
const methods = new Set();
for (const m of js.matchAll(/^\s{2}(?:async\s+)?([A-Za-z_$][\w$]*)\s*\(/gm)) methods.add(m[1]);

for (const h of handlers) {
  if (!methods.has(h)) errors.push(`wxml 绑定的事件 <${h}> 在 js 里没有定义`);
}

/* ------------------------------------------------------------------ data 表 */
function collectDataKeys(src) {
  const keys = new Set();
  const start = src.indexOf('data: {');
  if (start < 0) return keys;
  let i = src.indexOf('{', start);
  let depth = 0;
  let lineStart = true;
  for (; i < src.length; i += 1) {
    const c = src[i];
    if (c === '{' || c === '[') depth += 1;
    else if (c === '}' || c === ']') {
      depth -= 1;
      if (depth === 0) break;
    } else if (c === '\n') lineStart = true;
    if (depth === 1 && lineStart) {
      const rest = src.slice(i);
      const m = rest.match(/^([A-Za-z_$][\w$]*)\s*:/);
      if (m) keys.add(m[1]);
    }
    if (!/\s/.test(c)) lineStart = false;
    else if (c === '\n') lineStart = true;
  }
  return keys;
}

const dataKeys = collectDataKeys(js);
for (const m of js.matchAll(/setData\(\s*\{([^}]*)\}/g)) {
  for (const k of m[1].matchAll(/(?:^|,)\s*([A-Za-z_$][\w$]*)\s*[:,}]/g)) {
    const key = k[1];
    if (!dataKeys.has(key)) errors.push(`setData({ ${key} }) 的字段没有在 data 里声明`);
  }
}

/* ------------------------------------------------------------ wxml 表达式 */
const localVars = new Set(['item', 'index', 'true', 'false', 'null', 'undefined', 'length']);
for (const m of wxml.matchAll(/wx:for-item\s*=\s*"([^"]+)"/g)) localVars.add(m[1].trim());
for (const m of wxml.matchAll(/wx:for-index\s*=\s*"([^"]+)"/g)) localVars.add(m[1].trim());

// 把 {{}} 里的字符串字面量挖掉，避免把 'overflow: hidden' 里的 hidden 当成 data 字段
const stripLiterals = (expr) => expr.replace(/'[^']*'/g, "''").replace(/"[^"]*"/g, '""');
const isCompareOperand = (expr, idx) => /(===|!==|==|!=)\s*$/.test(expr.slice(0, idx));

const usedVars = new Set();
for (const m of wxml.matchAll(/\{\{([^}]*)\}\}/g)) {
  const expr = stripLiterals(m[1]);
  for (const id of expr.matchAll(/[A-Za-z_$][\w$]*/g)) {
    const name = id[0];
    const before = expr.slice(0, id.index).trimEnd();
    if (before.endsWith('.')) continue; // obj.field 的 field
    usedVars.add(name);
  }
}

for (const v of usedVars) {
  if (localVars.has(v)) continue;
  if (!dataKeys.has(v)) errors.push(`wxml 用到 {{${v}}}，但 data 里没有这个字段`);
}

/* ----------------------------------------------------------------- class 表 */
const wxmlClasses = new Set();
for (const m of wxml.matchAll(/\bclass\s*=\s*"([^"]*)"/g)) {
  const raw = m[1];
  const dyn = [];
  const statics = raw.replace(/\{\{([^}]*)\}\}/g, (_, expr) => {
    dyn.push(expr);
    return ' ';
  });
  for (const t of statics.split(/\s+/)) if (t) wxmlClasses.add(t);
  for (const expr of dyn) {
    for (const lit of expr.matchAll(/'([^']*)'|"([^"]*)"/g)) {
      if (isCompareOperand(expr, lit.index)) continue; // 'fit' === pageMode 里的 'fit' 不是类名
      const val = lit[1] !== undefined ? lit[1] : lit[2];
      for (const t of String(val).split(/\s+/)) if (t) wxmlClasses.add(t);
    }
  }
}

// js 里用选择器查过的 class（如 selectAll('.cell--pic')）不要求有样式定义
const jsClasses = new Set();
for (const m of js.matchAll(/['"`]\.([-\w]+)/g)) jsClasses.add(m[1]);

const cssClasses = new Set();
for (const src of [appWxss, wxss]) {
  if (!src) continue;
  const noComment = src.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const m of noComment.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) cssClasses.add(m[1]);
}

for (const c of wxmlClasses) {
  if (!cssClasses.has(c) && !jsClasses.has(c)) warnings.push(`wxml 用了 .${c}，但 wxss 里没有定义`);
}
const unused = [...cssClasses].filter((c) => !wxmlClasses.has(c) && !jsClasses.has(c) && !c.startsWith('bg'));
if (unused.length) infos.push(`wxss 里未被 wxml 使用的 class：${unused.join(', ')}`);

/* -------------------------------------------------------------------- 输出 */
const rel = (p) => path.relative(root, p).replace(/\\/g, '/');
console.log(`检查 ${rel(abs(target))}.{wxml,js,wxss}`);
console.log(
  `  事件 ${handlers.size} 个 · data 字段 ${dataKeys.size} 个 · wxml class ${wxmlClasses.size} 个 · wxss class ${cssClasses.size} 个`
);

for (const e of errors) console.log(`  [x] ${e}`);
for (const w of warnings) console.log(`  [!] ${w}`);
for (const i of infos) console.log(`  [i] ${i}`);
if (!errors.length && !warnings.length) console.log('  [√] 全部通过');

process.exit(errors.length);
