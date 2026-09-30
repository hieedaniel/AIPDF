# 组件配方（可直接抄）

所有配方假定已定义 `references/tokens.md` 里的变量。单位用 `rpx`（小程序自适应宽度）。

## 1. 背景层（层次感的来源）

不做背景层，白卡片就只能贴在灰底上，永远像表格。

```html
<view class="bg">
  <view class="bg__blob bg__blob--1"></view>
  <view class="bg__blob bg__blob--2"></view>
  <view class="bg__blob bg__blob--3"></view>
</view>
```

```css
page { background: var(--g-soft); }

.bg {
  position: fixed;
  left: 0; top: 0; right: 0; bottom: 0;
  overflow: hidden;         /* 光斑越界不产生横向滚动 */
  pointer-events: none;     /* 不吃手势 */
  z-index: 0;
}
.bg__blob { position: absolute; border-radius: 50%; filter: blur(60rpx); opacity: .55; }
.bg__blob--1 { width: 420rpx; height: 420rpx; left: -120rpx; top: -80rpx;  background: #FFD3E2; animation: drift 14s ease-in-out infinite; }
.bg__blob--2 { width: 360rpx; height: 360rpx; right: -110rpx; top: 260rpx; background: #DCCBFF; animation: drift 18s ease-in-out infinite reverse; }
.bg__blob--3 { width: 520rpx; height: 520rpx; left: 90rpx; bottom: -180rpx; background: #CDE6FF; opacity: .38; }

@keyframes drift {
  0%, 100% { transform: translate3d(0, 0, 0) scale(1); }
  50%      { transform: translate3d(24rpx, -30rpx, 0) scale(1.06); }
}

/* 内容必须抬到背景之上 */
.page { position: relative; z-index: 1; }
```

## 2. 卡片

```css
.card {
  margin-top: var(--sp-2);
  padding: var(--sp-3);
  border-radius: var(--r-lg);
  background: var(--c-surface);
  box-shadow: var(--sh-card);
  animation: fade-up var(--d-base) var(--ease) both;
}
.card__head { display: flex; align-items: center; justify-content: space-between; }
.card__title { display: flex; align-items: center; font-size: 30rpx; font-weight: 600; color: var(--c-text-1); }
.card__title::before {              /* 标题前的粉色小圆点，比下划线更精致 */
  content: '';
  width: 12rpx; height: 12rpx; margin-right: 12rpx;
  border-radius: 50%;
  background: var(--g-primary);
  box-shadow: 0 0 0 6rpx var(--c-primary-soft);
}
```

## 3. 胶囊按钮（主 / 次 / 幽灵）

```html
<button class="btn btn--primary" hover-class="btn--press" loading="{{generating}}" disabled="{{generating}}">✨ 生成 PDF</button>
<button class="btn btn--ghost" hover-class="btn--press">＋ 添加图片</button>
```

```css
.btn {
  width: 100%; height: 96rpx; line-height: 96rpx;
  margin: 0; padding: 0;
  font-size: 30rpx; font-weight: 600;
  border: none; border-radius: var(--r-pill);
  transition: transform var(--d-fast) var(--ease), opacity var(--d-fast);
}
.btn::after { border: none; }                 /* 干掉原生 button 的 1px 边框 */
.btn--press { transform: scale(.965); }       /* hover-class：按压回弹 */

.btn--primary {
  color: #fff;
  background: var(--g-primary);
  box-shadow: var(--sh-btn);
}
.btn--ghost {
  color: var(--c-primary-deep);
  background: var(--c-surface);
  box-shadow: var(--sh-card), var(--sh-ring);
}
.btn[disabled] {                              /* 禁用态不要用纯灰 */
  color: #fff;
  background: #E9DDE4;
  box-shadow: none;
  opacity: 1;
}
```

## 4. 悬浮操作区（底部）

```css
.footer {
  position: fixed; left: 0; right: 0; bottom: 0;
  padding: var(--sp-2) var(--sp-3) calc(var(--sp-3) + env(safe-area-inset-bottom));
  background: rgba(255, 249, 251, .97);
  border-radius: var(--r-lg) var(--r-lg) 0 0;   /* 顶部圆角，比直角柔和 */
  box-shadow: 0 -12rpx 32rpx rgba(236, 92, 144, .10);
}
.page { padding: var(--sp-3) var(--sp-3) calc(420rpx + env(safe-area-inset-bottom)); }
```

## 5. 拍立得照片格（相册类产品的招牌组件）

白框 + 底部留白 + 轻微旋转，比普通圆角图片「贵」得多。

```html
<view class="cell cell--pic" style="{{dragging ? 'left:..px;top:..px;width:..px;height:..px;' : ''}}">
  <view class="cell__box">
    <image class="cell__img" src="{{item.path}}" mode="aspectFill" style="transform: rotate({{item.rotate}}deg)" />
  </view>
  <view class="cell__no">1</view>
  <view class="cell__rot">⟳</view>
  <view class="cell__del">✕</view>
</view>
```

```css
.grid { display: flex; flex-wrap: wrap; }
.cell {
  position: relative;
  width: calc((100% - 32rpx) / 3);      /* 一行三个，间隙 16rpx */
  margin: 0 16rpx 16rpx 0;
  padding: 10rpx 10rpx 0;
  border-radius: var(--r-sm);
  background: #fff;
  box-shadow: 0 6rpx 18rpx rgba(236, 92, 144, .12);
  transition: transform var(--d-fast) var(--ease);
}
.cell:nth-child(3n) { margin-right: 0; }
.cell:active { transform: scale(.97); }

.cell__box {                            /* 正方形：padding-top 撑高，兼容性比 aspect-ratio 好 */
  position: relative;
  width: 100%;
  padding-top: 100%;
  border-radius: 14rpx;
  overflow: hidden;
  background: var(--g-photo);
}
.cell__img { position: absolute; left: 0; top: 0; width: 100%; height: 100%; }
.cell__no {                             /* 相纸下沿的编号 */
  height: 40rpx; line-height: 40rpx;
  font-size: 20rpx; text-align: center;
  color: var(--c-text-2);
  font-variant-numeric: tabular-nums;
}
.cell__rot, .cell__del {                /* 白底圆形小按钮，压在照片上 */
  position: absolute; top: 16rpx;
  width: 48rpx; height: 48rpx; line-height: 48rpx;
  border-radius: 50%; text-align: center; font-size: 26rpx;
  background: rgba(255, 255, 255, .88);
  color: var(--c-primary-deep);
  box-shadow: 0 4rpx 12rpx rgba(236, 92, 144, .22);
}
.cell__rot { left: 16rpx; }
.cell__del { right: 16rpx; }

/* 拖动中的格子：脱离文档流跟手 */
.grid .cell--dragging {
  position: fixed; z-index: 20; margin: 0;
  transform: rotate(-2deg) scale(1.08);
  box-shadow: var(--sh-float);
}

/* 排序模式：呼吸环（用独立子元素，避免与格子自身 transform/box-shadow 打架） */
.cell__ring { position: absolute; left: 0; top: 0; right: 0; bottom: 0; border-radius: var(--r-sm); pointer-events: none; animation: ring 1.6s ease-in-out infinite; }
@keyframes ring {
  0%, 100% { box-shadow: 0 0 0 0 rgba(255, 127, 168, .45); }
  50%      { box-shadow: 0 0 0 10rpx rgba(255, 127, 168, 0); }
}
```

## 6. 空状态插画（纯 CSS，无需素材）

```html
<view class="empty" bindtap="chooseImages">
  <view class="empty__art">
    <view class="empty__card empty__card--l"></view>
    <view class="empty__card empty__card--r"></view>
    <view class="empty__card empty__card--c"><text class="empty__plus">＋</text></view>
  </view>
  <view class="empty__title">还没有照片</view>
  <view class="empty__desc">点这里，把回忆装进一本小册子</view>
</view>
```

```css
.empty {
  padding: 44rpx 0 48rpx;
  border-radius: var(--r-md);
  background: linear-gradient(150deg, #FFF6FA 0%, #F8F2FF 100%);
  box-shadow: inset 0 0 0 2rpx rgba(255, 127, 168, .18);   /* 虚线太土，用浅色内描边 */
  text-align: center;
}
.empty__art { position: relative; height: 150rpx; margin-bottom: 18rpx; }
.empty__card {
  position: absolute; left: 50%; top: 0;
  width: 108rpx; height: 132rpx;
  border-radius: 12rpx; background: #fff;
  box-shadow: 0 8rpx 20rpx rgba(236, 92, 144, .14);
}
.empty__card--c { transform: translateX(-50%) rotate(-2deg); }
.empty__card--l { transform: translateX(-50%) translateX(-58rpx) rotate(-13deg); opacity: .75; }
.empty__card--r { transform: translateX(-50%) translateX(58rpx) rotate(12deg); opacity: .75; }
.empty__plus { position: absolute; left: 0; right: 0; top: 38rpx; font-size: 44rpx; color: var(--c-primary); }
```

## 7. 分段选择（排版模式这类二选一）

```css
.opts { display: flex; }
.opt {
  flex: 1; padding: 22rpx;
  border-radius: var(--r-md);
  background: var(--c-surface-soft);
  transition: all var(--d-fast) var(--ease);
}
.opt + .opt { margin-left: var(--sp-1); }
.opt--on {
  background: linear-gradient(150deg, #FFF1F6 0%, #F4EEFF 100%);
  box-shadow: var(--sh-card), var(--sh-ring);
}
.opt--on .opt__name { color: var(--c-primary-deep); }
.opt__tick {                 /* 选中角标：勾 + 粉色圆底 */
  position: absolute; right: 14rpx; top: 14rpx;
  width: 34rpx; height: 34rpx; line-height: 34rpx;
  border-radius: 50%; text-align: center;
  font-size: 22rpx; color: #fff; background: var(--c-primary);
}
```

## 8. 进度条 / 计数徽标

```html
<view class="bar"><view class="bar__fill" style="width:{{images.length / maxCount * 100}}%"></view></view>
```

```css
.bar { height: 10rpx; border-radius: var(--r-pill); background: var(--c-primary-soft); overflow: hidden; }
.bar__fill { height: 100%; border-radius: var(--r-pill); background: var(--g-progress); transition: width var(--d-base) var(--ease); }
.pill {
  padding: 4rpx 16rpx; border-radius: var(--r-pill);
  font-size: 22rpx; color: var(--c-primary-deep); background: var(--c-primary-soft);
  font-variant-numeric: tabular-nums;
}
```

## 9. Hero（首屏氛围）

```css
.hero {
  position: relative; overflow: hidden;
  padding: 40rpx 32rpx 44rpx;
  border-radius: var(--r-lg);
  background: var(--g-primary);
  box-shadow: var(--sh-float);
}
.hero::after {                    /* 右上角高光，制造「玻璃反光」感 */
  content: '';
  position: absolute; right: -80rpx; top: -100rpx;
  width: 320rpx; height: 320rpx; border-radius: 50%;
  background: rgba(255, 255, 255, .22);
}
.hero__title { position: relative; z-index: 1; font-size: 46rpx; font-weight: 700; color: #fff; letter-spacing: 1rpx; }
.hero__sub { position: relative; z-index: 1; margin-top: 10rpx; font-size: 24rpx; color: rgba(255, 255, 255, .9); line-height: 1.6; }
.hero__dev {                      /* 调试信息做成半透明玻璃胶囊 */
  display: inline-block; margin-top: 20rpx; padding: 8rpx 18rpx;
  border-radius: var(--r-pill);
  background: rgba(255, 255, 255, .22);
  font-size: 20rpx; color: #fff;
}
```

## 10. 可爱点缀（克制使用）

```css
/* 花瓣：两个圆角矩形旋转叠加，比 emoji 精致 */
.petal {
  position: absolute; width: 56rpx; height: 56rpx;
  border-radius: 50% 0 50% 50%;
  background: rgba(255, 255, 255, .32);
}
/* 星星：用一段 CSS 拼四角星，或直接用「✦」字符 */
.spark { color: #fff; opacity: .75; font-size: 22rpx; }
```
