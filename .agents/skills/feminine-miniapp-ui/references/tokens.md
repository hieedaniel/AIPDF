# 设计 Token（Creamy Blossom）

WXSS 支持 CSS 自定义属性（`var()`），推荐在页面 wxss 顶部定义一份 `:root`/`page` 作用域变量，
组件里只写 `var(--c-primary)`，换肤时只改变量。

> 可直接引入的版本见 `../assets/tokens.wxss`（同一份内容，独立文件方便 `@import`）。

## 1. 色板

| 变量 | 值 | 用途 |
| --- | --- | --- |
| `--c-bg-1` | `#FFF5F8` | 背景主调（暖奶油粉） |
| `--c-bg-2` | `#FBF3FF` | 背景次调（藕紫） |
| `--c-bg-3` | `#F5F8FF` | 背景点缀（冷调，用于光斑） |
| `--c-surface` | `#FFFFFF` | 卡片 |
| `--c-surface-soft` | `#FFF9FB` | 次级卡片 / 输入框底 |
| `--c-primary` | `#FF7FA8` | 主色：**只做填充**（光斑、浅底里的圆点），不用于文字/图标 |
| `--c-primary-deep` | `#C22A5F` | 主色深：**所有文字与图标用色**（白底 5.54:1，粉底 4.70:1） |
| `--c-primary-soft` | `#FFE6EF` | 主色浅（选中态底、标签底） |
| `--c-lilac` | `#B69CFF` | 辅助紫（渐变终点、第二强调） |
| `--c-lilac-soft` | `#F0E9FF` | 紫色浅底 |
| `--c-peach` | `#FFB58C` | 蜜桃（警示/暖色点缀） |
| `--c-mint` | `#7ED3BE` | 薄荷（成功态） |
| `--c-text-1` | `#4B3A50` | 标题/正文（梅子棕紫，替代纯黑）9.5:1 |
| `--c-text-2` | `#75627E` | 次要文字，白底 5.5:1 / 浅底 4.9:1，**说明文字一律用这级** |
| `--c-text-3` | `#9B8EA3` | 仅装饰（分隔符 `›`），不要承载信息 |
| `--c-line` | `#F3E7EF` | 分隔线（极浅，能不用就不用） |
| `--c-danger` | `#CF3A63` | 删除/危险（白底 4.73:1） |

> **铁律：浅彩色只做填充，深彩色做油墨。** 同一个色相准备两级——亮的那级铺面积（占比可大），
> 深的那级写文字/画图标。只用亮色做文字是少女风 UI 最典型的可读性崩塌点。

## 2. 渐变

| 变量 | 值 | 用途 |
| --- | --- | --- |
| `--g-primary` | `linear-gradient(135deg, #DD4B80 0%, #CD3F74 55%, #9B4FD8 100%)` | 主按钮、进度条（白字 3.89:1） |
| `--g-hero` | `linear-gradient(140deg, #FFF1F7 0%, #FDEFFA 45%, #F3EEFF 100%)` | Hero 底（**浅底 + 深字**，深字 9.15:1） |
| `--g-soft` | `linear-gradient(160deg, #FFF7FA 0%, #FBF2FF 55%, #F4F7FF 100%)` | 页面底色 |
| `--g-progress` | `linear-gradient(90deg, #FFA8C4 0%, #DD4B80 50%, #9B4FD8 100%)` | 进度条填充 |
| `--g-photo` | `linear-gradient(150deg, #FFF3F7 0%, #F6F1FF 100%)` | 图片占位底 |

**Hero 不要用「饱和粉渐变 + 白字」。** 这是最诱人也最容易翻车的组合：
白字压在 `#FF7FA8` 上只有 **2.37:1**，大字也达不到 3:1，实机上看起来就是“糊”。
正确做法是**浅色渐变底 + 梅子色字**（本方案的 `--g-hero` 配 `--c-text-1`，实测 9.15:1），
粉色留在光斑、按钮、徽标这些小面积上。

主按钮的白字实测 3.89:1（15.6px/600），低于正文 4.5 但在“粗体按钮”场景可读且视觉有力，
属有意取舍；若项目要求严格 AA，把渐变整体再压深到 `#C9417A` 起（约 4.65:1）。

## 3. 阴影（一律带主色相）

| 变量 | 值 |
| --- | --- |
| `--sh-card` | `0 12rpx 32rpx rgba(236, 92, 144, .10), 0 2rpx 6rpx rgba(75, 58, 80, .04)` |
| `--sh-float` | `0 18rpx 44rpx rgba(236, 92, 144, .22)` |
| `--sh-btn` | `0 14rpx 28rpx rgba(236, 92, 144, .30)` |
| `--sh-ring` | `inset 0 0 0 2rpx rgba(255, 127, 168, .16)` |

## 4. 圆角与间距

| 变量 | 值 | 说明 |
| --- | --- | --- |
| `--r-xs` | `12rpx` | 小标签 |
| `--r-sm` | `20rpx` | 缩略图内图 |
| `--r-md` | `28rpx` | 次级卡片、选项 |
| `--r-lg` | `36rpx` | 主卡片、Hero |
| `--r-pill` | `999rpx` | 按钮、标签、开关 |
| `--sp-1` … `--sp-5` | `8 / 16 / 24 / 32 / 48rpx` | 间距梯度（只用这 5 档） |

页面左右留白固定 `--sp-3`（24rpx）；卡片内边距 `--sp-3`；卡片之间 `--sp-2`（16rpx）。

## 5. 字体层级

| 名称 | 字号 / 行高 / 字重 / 颜色 | 用途 |
| --- | --- | --- |
| `.t-hero` | 46rpx / 1.25 / 700 / `--c-text-1`（浅底上） | Hero 大标题 |
| `.t-title` | 30rpx / 1.4 / 600 / `--c-text-1` | 卡片标题 |
| `.t-body` | 28rpx / 1.6 / 400 / `--c-text-1` | 正文 |
| `.t-sub` | 24rpx / 1.5 / 400 / `--c-text-2` | 说明 |
| `.t-weak` | 22rpx / 1.4 / 400 / `--c-text-3` | 辅助、时间戳 |
| `.t-num` | 等宽数字：`font-variant-numeric: tabular-nums` | 计数、页码 |

字族：小程序端不要依赖网络字体，用系统字体栈即可

```css
font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
```

## 6. 动效

| 变量 | 值 | 用途 |
| --- | --- | --- |
| `--d-fast` | `150ms` | 按下回弹 |
| `--d-base` | `220ms` | 进入淡入 |
| `--d-slow` | `1600ms` | 较长过渡（呼吸环、进度条） |
| `--ease` | `cubic-bezier(.32, .72, 0, 1)` | 统一缓动（iOS 风） |
| `--anim-loop` | `infinite`（减动态时 `1`） | 所有无限循环动画的迭代次数；**不要写死 `infinite`**，否则「减少动态效果」开关会失效 |

无限循环动画（光斑漂移、呼吸环）一律写成 `animation: drift 16s ease-in-out var(--anim-loop);`，
并在 `@media (prefers-reduced-motion: reduce)` 里把 `--d-*` 置成 `1ms`、`--anim-loop` 置成 `1`：
时长极短 + 只跑一次 = 等于关掉，不需要逐个覆盖。

**动画只动 `transform` / `opacity`。** 尤其不要动画 `box-shadow`——排序模式下 30 个格子同时呼吸
会变成每帧全量重绘，在中低端机上直接掉帧；改成给一个固定边框的子元素调 `opacity` 即可。

进入动画统一写法（避免 `animation-fill-mode` 抖动）：

```css
@keyframes fade-up {
  from { opacity: 0; transform: translateY(16rpx); }
  to   { opacity: 1; transform: translateY(0); }
}
.card { animation: fade-up var(--d-base) var(--ease) both; }
```

## 7. 无障碍 / 可用性底线

- 正文（≤28rpx）与背景对比度 ≥ **4.5:1**；≥30rpx 粗体或 ≥36rpx 才可放宽到 3:1。
- **浅彩底上用深同色系文字，不要用白字**；非要用白字，渐变每个色标都要 ≥3:1（含最浅那端）。
- 可点区域 ≥ 88rpx × 88rpx（缩略图角标按钮 ≥ 56rpx 且加 `catchtap` 防误触）。
- 不用颜色单独传达状态：删除用 `✕`，成功用对勾 + 文案。
- 尊重「减少动态效果」：把长动画放在 `@media (prefers-reduced-motion: reduce)` 里关掉。

### 交付前必跑的两个脚本

```bash
# 1) 结构校验：wxml 绑定的事件/data/class 是否都存在，wxss 有没有写了没人用的类
node scripts/check-miniapp.js

# 2) 视觉验收：真实渲染后量横向溢出、列数、格子等高、文字裁剪、底部栏遮挡、实际对比度
node scripts/preview-miniapp.js            # 同时输出 var/preview-miniapp.png 供人眼复查
```

对比度不要凭感觉——`preview-miniapp.js` 会逐元素向上找背景（包括渐变，按每个色标取最差值）算实测值。
