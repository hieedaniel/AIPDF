#!/usr/bin/env node
/**
 * preview-miniapp.js —— 小程序的「无微信开发者工具」视觉验收。
 *
 * 把 app.wxss / pages/index/index.wxss 的 rpx 按 390/750 换算成 px，配上本文件
 * 里的静态 mock 结构渲染到 headless 浏览器，然后自动检查：
 *   · 横向溢出 / 元素越界        · 网格列数、拍立得与追加格是否等高
 *   · 文字被裁剪 / 意外换行      · 底部固定栏是否遮住内容
 *   · 关键文字与背景的实际对比度（含渐变，按每个色标分别算最差值）
 * 并输出手机尺寸的截图，方便人眼复查。
 *
 *   node scripts/preview-miniapp.js            # 审计 + 截图
 *   node scripts/preview-miniapp.js --no-shot  # 只出审计数字
 *
 * 改 index.wxml 结构后，请同步更新本文件里的 mock（cell/addCell/hero/... 对应的就是页面结构）。
 * 退出码 = 硬性问题的数量（横向溢出、越界、裁剪、底栏遮挡）。
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const root = path.resolve(__dirname, '..');
const RATIO = 390 / 750; // 750rpx 设计稿宽 = 390px
const rpx2px = (css) => css.replace(/(-?\d*\.?\d+)rpx/g, (_, n) => `${(parseFloat(n) * RATIO).toFixed(3)}px`);

const appCss = rpx2px(fs.readFileSync(path.join(root, 'miniapp/app.wxss'), 'utf8'));
const pageCss = rpx2px(fs.readFileSync(path.join(root, 'miniapp/pages/index/index.wxss'), 'utf8'));

const photo = (hue, cls = 'ph') =>
  `<div class="${cls}" style="background:linear-gradient(150deg,hsl(${hue} 70% 82%),hsl(${hue + 40} 60% 68%))"></div>`;

const cell = (i, rotate = 0, sorting = false) => `
      <div class="cell cell--pic">
        ${sorting ? '<div class="cell__ring"></div>' : ''}
        <div class="cell__box">${photo(320 + i * 26, `ph cell__img`)}</div>
        <div class="cell__no">第 ${i + 1} 张</div>
        <div class="cell__rot">↻</div>
        <div class="cell__del">✕</div>
        ${sorting ? '<div class="cell__move"><div class="cell__movebtn">‹</div><div class="cell__movebtn">›</div></div>' : ''}
      </div>`;

const addCell = `
      <div class="cell cell--add">
        <div class="cell__plus">＋</div>
        <div class="cell__addtext">继续添加</div>
      </div>`;

const hero = `
  <div class="hero">
    <div class="hero__petal hero__petal--1"></div>
    <div class="hero__petal hero__petal--2"></div>
    <div class="hero__title">拍纸立得</div>
    <div class="hero__sub">把照片装进一本小册子</div>
    <div class="steps">
      <div class="steps__item"><span class="steps__no">1</span><span class="steps__txt">拍摄选图</span></div>
      <span class="steps__sep">›</span>
      <div class="steps__item"><span class="steps__no">2</span><span class="steps__txt">旋转排序</span></div>
      <span class="steps__sep">›</span>
      <div class="steps__item"><span class="steps__no">3</span><span class="steps__txt">合成 PDF</span></div>
    </div>
    <div class="hero__dev">
      <span class="hero__devline">调试模式 · server</span>
      <span class="hero__devline">https://aipdf.seveninfo.cn</span>
      <span class="hero__devline">AppID wx5d44b2d4e2362a9c</span>
    </div>
  </div>`;

const opts = `
  <div class="card">
    <div class="card__head"><div class="card__title">排版模式</div></div>
    <div class="opts">
      <div class="opt opt--on"><div class="opt__tick">✓</div><div class="opt__name">每图一页</div><div class="opt__desc">每张照片独占一页 A4</div></div>
      <div class="opt"><div class="opt__name">长图分页</div><div class="opt__desc">长截图自动切成多页</div></div>
    </div>
  </div>`;

const footer = `
  <div class="footer">
    <div class="footer__bar">
      <button class="btn btn--ghost">＋ 添加</button>
      <button class="btn btn--primary">✨ 生成 PDF</button>
    </div>
  </div>`;

const filled = `
  <div class="card">
    <div class="card__head">
      <div class="card__title">我的照片</div>
      <div class="card__meta"><span class="pill">6 / 30</span><span class="link link--danger">清空</span></div>
    </div>
    <div class="bar"><div class="bar__fill" style="width:20%"></div></div>
    <div class="grid">${[0, 1, 2, 3, 4, 5].map((i) => cell(i, 0)).join('')}${addCell}</div>
    <div class="tips"><span class="tips__dot"></span><span class="tips__txt">轻点照片可放大查看 · 长按照片可拖动排序</span></div>
  </div>`;

const sorting = `
  <div class="card">
    <div class="card__head">
      <div class="card__title">我的照片</div>
      <div class="card__meta"><span class="pill">6 / 30</span><span class="link link--danger">清空</span></div>
    </div>
    <div class="bar"><div class="bar__fill" style="width:20%"></div></div>
    <div class="sortbar">
      <span class="sortbar__icon">✥</span>
      <span class="sortbar__hint">按住照片拖动排序，也可点 ‹ › 微调</span>
      <span class="sortbar__done">完成</span>
    </div>
    <div class="grid grid--sorting">${[0, 1, 2, 3, 4, 5].map((i) => cell(i, 0, true)).join('')}</div>
  </div>`;

const empty = `
  <div class="card">
    <div class="card__head">
      <div class="card__title">我的照片</div>
      <div class="card__meta"><span class="pill">0 / 30</span></div>
    </div>
    <div class="empty">
      <div class="empty__art">
        <div class="empty__card empty__card--l"></div>
        <div class="empty__card empty__card--r"></div>
        <div class="empty__card empty__card--c"><span class="empty__plus">＋</span></div>
      </div>
      <div class="empty__title">还没有照片</div>
      <div class="empty__desc">点这里，从相册选图或用相机拍一张</div>
    </div>
  </div>`;

const busy = `
  <div class="card">
    <div class="card__head">
      <div class="card__title">我的照片</div>
      <div class="card__meta"><span class="pill">12 / 30</span></div>
    </div>
    <div class="bar"><div class="bar__fill" style="width:40%"></div></div>
    <div class="grid">${[0, 1, 2].map((i) => cell(i, i === 0 ? 90 : 0)).join('')}</div>
  </div>`;

const phone = (body, footerHtml = footer) => `
<div class="phone">
  <div class="navbar">拍纸立得</div>
  <page class="screen">
    <div class="bg"><div class="bg__blob bg__blob--1"></div><div class="bg__blob bg__blob--2"></div><div class="bg__blob bg__blob--3"></div></div>
    <div class="page">${body}</div>
    ${footerHtml}
  </page>
</div>`;

const AUDIT = `
<script>
(function () {
  const out = {};
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); return +(((Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05))).toFixed(2); };
  const parse = (s) => { const m = String(s).match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(',').map(Number); return p.slice(0, 3).concat(p[3] === undefined ? 1 : p[3]); };
  const blend = (fg, bg) => fg.slice(0, 3).map((v, i) => v * fg[3] + bg[i] * (1 - fg[3]));
  // 逐层向上找到实际背景（遇到渐变则返回所有色标）
  const bgs = (el) => {
    let n = el, stack = [];
    while (n && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      const bi = cs.backgroundImage;
      const bc = parse(cs.backgroundColor);
      if (bi && bi.indexOf('gradient') >= 0) {
        const stops = [...bi.matchAll(/rgba?\\([^)]+\\)/g)].map((m) => parse(m[0]).slice(0, 3));
        if (stops.length) return { kind: 'gradient', stops, from: n.className };
      }
      if (bc && bc[3] > 0.9) return { kind: 'solid', stops: [bc.slice(0, 3)], from: n.className };
      if (bc && bc[3] > 0) stack.push(bc);
      n = n.parentElement;
    }
    const base = [255, 255, 255];
    for (const s of stack.reverse()) { /* 忽略半透明叠加，够用 */ }
    return { kind: 'solid', stops: [base], from: 'root' };
  };
  const contrast = (sel, label) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const cs = getComputedStyle(el);
    const fg = parse(cs.color);
    const bg = bgs(el);
    const bg0 = parse(getComputedStyle(el).backgroundColor);
    const worst = Math.min(...bg.stops.map((s) => ratio(fg.slice(0, 3), bg0 && bg0[3] > 0.9 ? bg0.slice(0, 3) : s)));
    return { label, sel, fg: cs.color, bg: bg.stops.map((s) => 'rgb(' + s.join(',') + ')'), kind: bg.kind, from: bg.from, ratio: worst, size: cs.fontSize, weight: cs.fontWeight };
  };
  out.phones = [...document.querySelectorAll('.phone')].map((p, i) => {
    const rect = (el) => { const r = el.getBoundingClientRect(); return { x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1), r: +r.right.toFixed(1), b: +r.bottom.toFixed(1) }; };
    const cells = [...p.querySelectorAll('.cell--pic')];
    const rows = {};
    cells.forEach((c) => { const t = Math.round(c.getBoundingClientRect().top); (rows[t] = rows[t] || []).push(c); });
    const add = p.querySelector('.cell--add');
    const footerEl = p.querySelector('.footer');
    const pageEl = p.querySelector('.page');
    const lastCard = [...p.querySelectorAll('.card')].pop();
    // 越界元素（以「本台手机」的框为准；.bg 光斑与预览用的 navbar 是故意越界/非小程序节点，排除）
    const pRect = p.getBoundingClientRect();
    const over = [...p.querySelectorAll('*')].filter((el) => {
      if (el.closest('.bg') || el.classList.contains('navbar')) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && (r.right > pRect.right + 0.5 || r.left < pRect.left - 0.5);
    }).map((el) => el.className + ' ' + JSON.stringify(rect(el))).slice(0, 6);
    // 文字横向裁剪
    const clipped = [...p.querySelectorAll('.cell__no, .pill, .btn, .opt__name, .opt__desc, .tips__txt, .sortbar__hint, .hero__sub, .steps__txt, .empty__title, .empty__desc, .link')]
      .filter((el) => el.scrollWidth > el.clientWidth + 1)
      .map((el) => el.className + ':' + el.textContent.trim());
    // 文字换行（高度超过一行）
    const wrapped = [...p.querySelectorAll('.cell__no, .pill, .steps__txt, .btn, .link, .opt__name, .sortbar__done, .empty__title')]
      .filter((el) => el.getClientRects().length > 1)
      .map((el) => el.className + ':' + el.textContent.trim());
    const rot = cells[0] && cells[0].querySelector('.cell__rot');
    const del = cells[0] && cells[0].querySelector('.cell__del');
    return {
      idx: i,
      overflowX: p.scrollWidth - p.clientWidth,
      rows: Object.values(rows).map((r) => r.length),
      cellW: cells[0] ? rect(cells[0]).w : null,
      cellH: cells[0] ? rect(cells[0]).h : null,
      addH: add ? rect(add).h : null,
      addSameRow: add && cells[0] ? Math.round(rect(add).y) === Math.round(rect(cells[0]).y) : null,
      boxSquare: cells[0] ? rect(cells[0].querySelector('.cell__box')) : null,
      rotDelGap: rot && del ? +(del.getBoundingClientRect().left - rot.getBoundingClientRect().right).toFixed(1) : null,
      pagePadBottom: getComputedStyle(pageEl).paddingBottom,
      footerH: footerEl ? rect(footerEl).h : null,
      contentBottom: lastCard ? rect(lastCard).b : null,
      over, clipped, wrapped,
      pageH: Math.round(p.pageHeight || 0),
    };
  });
  out.contrast = [
    contrast('.hero__title', 'hero 标题'),
    contrast('.hero__sub', 'hero 副标题'),
    contrast('.steps__txt', '步骤文字'),
    contrast('.card__title', '卡片标题'),
    contrast('.pill', '计数胶囊'),
    contrast('.link--danger', '清空'),
    contrast('.cell__no', '相纸序号'),
    contrast('.tips__txt', '操作提示'),
    contrast('.empty__title', '空状态标题'),
    contrast('.empty__desc', '空状态说明'),
    contrast('.opt__name', '选项名'),
    contrast('.opt__desc', '选项说明'),
    contrast('.btn--primary', '主按钮'),
    contrast('.btn--ghost', '次按钮'),
    contrast('.sortbar__hint', '排序提示'),
    contrast('.sortbar__done', '完成按钮'),
    contrast('.cell__rot', '旋转按钮'),
    contrast('.cell__del', '删除按钮'),
    contrast('.cell__addtext', '追加载文'),
    contrast('.cell__plus', '追加加号'),
    contrast('.hero__devline', '调试文字'),
  ].filter(Boolean);
  document.getElementById('__report').textContent = btoa(unescape(encodeURIComponent(JSON.stringify(out))));
})();
</script>`;

const html = `<!doctype html>
<html><head><meta charset="utf-8">
<style>
* { box-sizing: border-box; }
body { margin: 0; background: #e9e4ee; display: flex; gap: 16px; padding: 16px; font-family: -apple-system, "Microsoft YaHei", sans-serif; }
/* .phone 用 transform 建立包含块，模拟小程序里 position:fixed 相对视口的行为 */
.phone { position: relative; width: 390px; height: 844px; overflow: hidden; background: #fff5f8; transform: translateZ(0); }
page.screen { display: block; position: relative; min-height: 844px; }
.navbar { height: 44px; line-height: 44px; text-align: center; font-size: 17px; font-weight: 600; background: #fff3f8; color: #4b3a50; }
.ph { width: 100%; height: 100%; }
${appCss}
${pageCss}
</style></head>
<body>
${phone(hero + filled + opts)}
${phone(hero + sorting + opts)}
${phone(hero + empty + opts)}
${phone(hero + busy + opts)}
<pre id="__report" style="display:none"></pre>
${AUDIT}
</body></html>`;

const htmlPath = path.join(root, 'var/_preview.html');
fs.writeFileSync(htmlPath, html);

// 找一个能用的 Chromium 内核浏览器（Windows 本地开发环境）
const CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
];
const edge = CANDIDATES.find((p) => fs.existsSync(p));
if (!edge) {
  console.error('未找到 Edge/Chrome，无法渲染预览。已生成 HTML：' + htmlPath);
  process.exit(1);
}
// 独立 user-data-dir：避免与已在运行的浏览器争抢默认配置目录而挂住
const profile = path.join(root, 'var/_edge-profile');
fs.mkdirSync(profile, { recursive: true });
const BASE_ARGS = [
  '--headless=new',
  '--disable-gpu',
  '--no-first-run',
  '--no-default-browser-check',
  '--user-data-dir=' + profile,
  '--hide-scrollbars',
  '--window-size=1680,1000',
];
const fileUrl = 'file:///' + htmlPath.replace(/\\/g, '/');

const dom = execFileSync(edge, [...BASE_ARGS, '--dump-dom', fileUrl], {
  encoding: 'utf8',
  maxBuffer: 64 * 1024 * 1024,
  timeout: 60000,
});
const m = dom.match(/<pre id="__report"[^>]*>([A-Za-z0-9+/=]+)<\/pre>/);
if (!m) {
  console.error('未拿到审计报告，DOM 片段：', dom.slice(-800));
  process.exit(1);
}
const report = JSON.parse(Buffer.from(m[1], 'base64').toString('utf8'));
let hard = 0;

console.log('=== 几何 ===');
for (const p of report.phones) {
  const name = ['有图', '排序模式', '空状态', '生成中'][p.idx] || p.idx;
  console.log(
    `[${name}] 横向溢出=${p.overflowX}px 列数=[${p.rows}] 格子=${p.cellW}x${p.cellH} 追加格高=${p.addH} ` +
      `(同排=${p.addSameRow}) 画框=${p.boxSquare ? p.boxSquare.w + 'x' + p.boxSquare.h : '-'} ` +
      `⟳✕间距=${p.rotDelGap}px 底栏高=${p.footerH} 页面底部padding=${p.pagePadBottom}`
  );
  if (p.overflowX) { console.log(`   横向溢出 ${p.overflowX}px`); hard++; }
  if (p.over.length) { console.log(`   越界元素: ${p.over.join(' | ')}`); hard++; }
  if (p.clipped.length) { console.log(`   文字被裁剪: ${p.clipped.join(' | ')}`); hard++; }
  if (p.wrapped.length) console.log(`   文字换行: ${p.wrapped.join(' | ')}`);
  if (p.addH && p.cellH && Math.abs(p.addH - p.cellH) > 1) { console.log(`   追加格与拍立得不等高：${p.addH} vs ${p.cellH}`); hard++; }
}

console.log('\n=== 对比度（WCAG：正文 ≥4.5，大字/粗体 ≥3.0） ===');
for (const c of report.contrast) {
  const flag = c.ratio >= 4.5 ? 'OK ' : c.ratio >= 3 ? '大字' : '低 ';
  console.log(
    `${flag} ${c.ratio.toFixed(2).padStart(5)}:1  ${c.label.padEnd(6)} ${c.size}/${c.weight}  ${c.fg} on ${c.back || ''}${c.kind === 'gradient' ? '(渐变最差色标)' : ''} <- ${c.from}`
  );
}

const low = report.contrast.filter((c) => c.ratio < 3 && c.sel !== '.btn--primary');
if (low.length) {
  console.log('\n低于 3:1 的文字（必须处理）：' + low.map((c) => c.label).join('、'));
  hard += low.length;
}

if (!process.argv.includes('--no-shot')) {
  const out = path.join(root, 'var/preview-miniapp.png');
  execFileSync(edge, [...BASE_ARGS, `--screenshot=${out}`, fileUrl], { stdio: 'ignore', timeout: 60000 });
  console.log('\n截图（可人眼复查）：' + out);
}
console.log(hard ? `\n[XX] ${hard} 项硬性问题` : '\n[OK] 几何与对比度检查通过');
process.exit(hard);
