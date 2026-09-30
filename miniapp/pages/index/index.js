// pages/index/index.js
//
// AI 拍纸立得 —— 小程序端主页面
// ---------------------------------------------------------------------------
// 关于「循环调用 wx.uploadFile」的重要说明：
//   wx.uploadFile 一次请求只能携带 **一个** 文件，若每张图都直接打向
//   /api/v1/convert-to-pdf，服务端会生成 N 个 PDF，无法按顺序合并成一份。
//   因此本页默认采用「分张上传 + 一次合并」的标准做法：
//     循环 wx.uploadFile → /api/v1/upload-image 拿到 image_id
//     → 按顺序把 image_id 数组提交给 /api/v1/convert-to-pdf-by-ids
//   若后端未部署这两个接口，可把 STRATEGY 改为 'base64'（一次性 JSON 请求）。
// ---------------------------------------------------------------------------

// ===========================================================================
//  环境配置：本地调试 / 真机调试 / 连服务器调试 / 正式环境 四选一
// ---------------------------------------------------------------------------
//  'local'        微信开发者工具【模拟器】调试，后端跑在同一台电脑上
//  'local-device' 真机预览 / 真机调试：手机必须和电脑连同一个 Wi-Fi
//  'server'       本地连【已部署的服务器】调试：HTTPS 域名已通，代码仍是调试态
//  'prod'         正式环境：必须 HTTPS，且已在“开发管理 → 服务器域名”配好白名单
// ===========================================================================
const ENV = 'server';

// 以下地址按需修改
const LOCAL_BASE_URL = 'http://127.0.0.1:8000'; // 模拟器用
const LAN_IP = '192.168.1.100'; // 真机用：改成你电脑的局域网 IP（运行 scripts/dev-server.ps1 会直接打印）
const SERVER_BASE_URL = 'https://aipdf.seveninfo.cn'; // 已部署服务器（ENV='server' 时用）
const PROD_BASE_URL = 'https://aipdf.seveninfo.cn'; // 正式域名（ENV='prod' 时用）

const BASE_URLS = {
  local: LOCAL_BASE_URL,
  'local-device': `http://${LAN_IP}:8000`,
  server: SERVER_BASE_URL,
  prod: PROD_BASE_URL,
};
const BASE_URL = BASE_URLS[ENV] || LOCAL_BASE_URL;

// ENV = 'local' / 'local-device' / 'server' 都算调试态：
//   开发者工具里需勾选「详情 → 本地设置 → 不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」
//   （ENV='server' 时若域名已加入小程序后台白名单，也可以不勾选，更贴近正式环境）
const IS_LOCAL = ENV !== 'prod';

const API = {
  // 方式一：分张上传（推荐，请求体小，支持大图/多图）
  uploadOne: `${BASE_URL}/api/v1/upload-image`,
  convertByIds: `${BASE_URL}/api/v1/convert-to-pdf-by-ids`,
  // 方式二：一次性 base64 JSON
  convertBase64: `${BASE_URL}/api/v1/convert-to-pdf-base64`,
  // 方式三：原生多文件 multipart（H5/curl 用；小程序无法一次传多文件）
  convertMultipart: `${BASE_URL}/api/v1/convert-to-pdf`,
  health: `${BASE_URL}/health`,
};

// 'uploadfile' = 循环 wx.uploadFile 分张上传后合并（默认）
// 'base64'     = 一次性 base64 JSON（代码最短，但请求体比原图大约 1/3）
const STRATEGY = 'uploadfile';

// 单次最多张数：本机默认值，onLoad 时会用 /health 返回的 limits 收紧到服务端生效值，
// 避免「手机允许 30 张、服务端只收 20 张」这种要到用户那里才暴露的错配。
const MAX_COUNT = 30;
// 微信 chooseMedia / chooseImage 的 count 上限就是 9，超过要分批追加
const CHOOSE_BATCH = 9;
// 上传并发数：串行 30 张太慢，全并发在弱网上容易超时且内存暴涨，取中间值
const UPLOAD_CONCURRENCY = 3;

const UPLOAD_TIMEOUT = 60 * 1000; // 单张上传超时
const REQUEST_TIMEOUT = 120 * 1000; // 合成 / 下载超时（张数多时合成更久）
const HEALTH_TIMEOUT = 6 * 1000; // 读服务端限制，失败就沿用本地默认值
const COMPRESS_QUALITY = 80; // 上传前压缩质量
const COMPRESS_MAX_WIDTH = 1600; // 上传前压缩后的长边像素
const PREVIEW_MAX_SIDE = 2400; // 预览用旋转时的像素上限（够放大看清，又不至于爆内存）
const MAX_CANVAS_PIXELS = 6 * 1000 * 1000; // 旋转画布的像素上限（极端长图会超机型 canvas 上限而导出空白）
const PDF_TITLE = 'AI拍纸立得';

// 排序模式：拖到上下边缘多近时开始自动滚动，以及每次滚多少像素
const EDGE_SCROLL_ZONE = 90;
const EDGE_SCROLL_STEP = 16;

/**
 * 有限并发执行，结果保持与入参同序。
 *
 * 串行发 30 张要等到天荒地老，一次性全发在弱网下会同时占满内存与连接，
 * 所以固定开 N 条「流水线」，每条自己从队列里取下一张。
 */
function mapLimit(items, limit, worker) {
  const total = items.length;
  const results = new Array(total);
  let cursor = 0;

  const runner = async () => {
    for (;;) {
      const index = cursor;
      cursor += 1;
      if (index >= total) return;
      results[index] = await worker(items[index], index); // 先占位再 await，天然保序
    }
  };

  const size = Math.max(1, Math.min(limit, total));
  const jobs = [];
  for (let i = 0; i < size; i += 1) jobs.push(runner());
  return Promise.all(jobs).then(() => results);
}

/** 取窗口高度：拖动时判断是否该自动滚动，以及算最大滚动距离 */
function windowHeight() {
  try {
    return wx.getWindowInfo().windowHeight;
  } catch (e) {
    const info = wx.getSystemInfoSync ? wx.getSystemInfoSync() : null;
    return (info && info.windowHeight) || 0;
  }
}

/** 震动反馈：不传回调的 wx API 失败会产生未处理的 Promise 拒绝，补齐 fail 更安静 */
function vibrate(type) {
  try {
    wx.vibrateShort({ type, fail: () => {} });
  } catch (e) {
    // 部分机型/系统不支持震动，忽略
  }
}

Page({
  data: {
    // [{ id, path, size, rotate }]，数组顺序 = PDF 排版顺序
    images: [],
    maxCount: MAX_COUNT,
    pageMode: 'fit', // fit=每图一页，split=长图自动分页
    generating: false, // 合成中：禁用所有按钮，避免重复提交
    progressText: '', // 显示在主按钮上的进度文案
    sorting: false, // 排序模式：长按缩略图进入，可拖动重排
    dragging: false, // 正在拖动（拖动中的格子跟手 + 悬浮）
    dragId: '', // 正在拖动的图片 id
    dragLeft: 0, // 拖动中的格子位置（视口坐标，px）
    dragTop: 0,
    cellW: 0,
    cellH: 0,
    isLocal: IS_LOCAL, // 调试态时在顶部展示当前后端地址
    env: ENV, // 当前环境，便于在 WXML 里做条件渲染
    baseUrl: BASE_URL,
    appId: '', // 运行时 AppID：域名白名单报错时用来校对“是不是配到了同一个小程序”
  },

  onLoad() {
    // 域名白名单是配在「某个小程序 AppID」名下的。如果配置的小程序和这里跑的不是
    // 同一个，白名单怎么配都没用 —— 所以把运行时 AppID 直接显示在调试条里。
    let appId = '';
    try {
      appId = (wx.getAccountInfoSync && wx.getAccountInfoSync().miniProgram.appId) || '';
    } catch (e) {
      appId = '';
    }

    // 处理结果缓存：不进 data（避免 setData 传大对象/长路径），只做内部查表
    this._processed = {}; // id -> { rotate, path } 压缩 + 旋转后的最终上传文件
    this._rotatedPreview = {}; // `${id}:${rotate}` -> 高分辨率旋转结果
    this._canvasNode = null; // 隐藏 canvas 节点（旋转用），只取一次
    this._canvasPromise = null;
    this._idSeed = 0;
    this._scrollTop = 0; // onPageScroll 记录，拖动时用
    this._rects = null; // 拖动时量取的所有缩略图位置
    this._dragScrollTop = 0;
    this._maxScrollTop = 0;

    this.setData({ appId });
    this.loadServerLimits();
  },

  onPageScroll(e) {
    const next = e.scrollTop || 0;

    // 拖动中：页面滚动（含边缘自动滚动）后要把命中区的坐标一起平移，
    // 以真实滚动值为准可以顺带修正 pageScrollTo 被截断带来的偏差
    if (this.data.dragging && this._rects) {
      const applied = next - (this._dragScrollTop || 0);
      if (applied) {
        this._dragScrollTop = next;
        this._rects.forEach((rect) => {
          rect.top -= applied;
          rect.bottom -= applied;
        });
        this.setData({ dragTop: (this._dragPageY || 0) - next - this._dragOffsetY });
      }
      return;
    }

    this._scrollTop = next;
  },

  /** 读服务端生效的限制（失败就沿用本地默认值，不打扰用户） */
  async loadServerLimits() {
    try {
      const body = await new Promise((resolve, reject) => {
        wx.request({
          url: API.health,
          method: 'GET',
          timeout: HEALTH_TIMEOUT,
          success: (res) => resolve(res && res.data),
          fail: reject,
        });
      });
      const limits = body && body.limits;
      const serverMax = Number(limits && limits.max_file_count) || 0;
      if (serverMax > 0) this.setData({ maxCount: Math.min(serverMax, MAX_COUNT) });
      if (body && body.warnings && body.warnings.length) {
        // 服务端自检有问题时，至少让开发者在调试时看得见
        console.warn('[AI拍纸立得] 服务端自检告警', body.warnings);
      }
    } catch (e) {
      console.warn('[AI拍纸立得] 读取服务端限制失败，使用本地默认值', e);
    }
  },

  /* =====================================================================
   *  一、选图 / 管理图片
   * ===================================================================== */

  /** 【添加图片 / 拍照】相册或相机，追加到数组末尾 */
  async chooseImages() {
    if (this.data.generating) return;

    const maxCount = this.data.maxCount || MAX_COUNT;
    const rest = maxCount - this.data.images.length;
    if (rest <= 0) {
      wx.showToast({ title: `最多 ${maxCount} 张`, icon: 'none' });
      return;
    }

    let res;
    try {
      res = await wx.chooseMedia({
        count: Math.min(rest, CHOOSE_BATCH), // 微信单次选图上限 9 张，超了要再点一次
        mediaType: ['image'],
        sourceType: ['album', 'camera'], // 相册 + 拍照
        sizeType: ['compressed'], // 让微信先压一道，省流量
        camera: 'back',
      });
    } catch (err) {
      // 用户主动取消不算错误，静默返回
      if (this.isCancel(err)) return;
      wx.showToast({ title: '打开相册失败', icon: 'none' });
      return;
    }

    const picked = (res && res.tempFiles ? res.tempFiles : [])
      .filter((f) => f && f.tempFilePath)
      .map((f) => this.makeImageItem(f.tempFilePath, f.size || 0));

    if (!picked.length) return;

    this.setData({ images: this.data.images.concat(picked) });
    vibrate('light');

    if (this.data.images.length >= maxCount) {
      wx.showToast({ title: `已达上限 ${maxCount} 张`, icon: 'none' });
    }
  },

  /** 生成一条图片记录；id 用于拖动排序/缓存查表（path 会变，不能当 key） */
  makeImageItem(path, size) {
    this._idSeed += 1;
    return { id: `img-${Date.now()}-${this._idSeed}`, path, size, rotate: 0 };
  },

  /** 删除单张（网格上的 ✕） */
  removeImage(e) {
    if (this.data.generating) return;

    const index = Number(e.currentTarget.dataset.index);
    const images = this.data.images.slice();
    if (index < 0 || index >= images.length) return;

    this.dropCache(images[index].id);
    images.splice(index, 1);
    this.setData({ images });
    vibrate('light');
  },

  /** 清空全部 */
  clearAll() {
    if (this.data.generating) return;

    wx.showModal({
      title: '清空全部图片？',
      content: '已选择的图片将被移除，PDF 不会被删除。',
      confirmText: '清空',
      confirmColor: '#fa5151',
      success: (res) => {
        if (res.confirm) {
          this._processed = {};
          this._rotatedPreview = {};
          this.setData({ images: [], sorting: false, dragging: false });
        }
      },
    });
  },

  /** 【旋转】每次顺时针 90°；缩略图用 CSS 即时跟随，所见即所得 */
  rotateImage(e) {
    if (this.data.generating) return;

    const index = Number(e.currentTarget.dataset.index);
    const images = this.data.images;
    if (index < 0 || index >= images.length) return;

    const next = images.slice();
    const item = next[index];
    next[index] = { ...item, rotate: ((item.rotate || 0) + 90) % 360 };

    this.dropCache(item.id); // 角度变了，之前处理好的文件作废
    this.setData({ images: next });
    vibrate('light');
  },

  /** 单张图片的处理缓存失效（旋转/删除后必须调，否则会用到旧角度的文件） */
  dropCache(id) {
    delete this._processed[id];
    Object.keys(this._rotatedPreview).forEach((key) => {
      if (key.indexOf(`${id}:`) === 0) delete this._rotatedPreview[key];
    });
  },

  /** 点击缩略图看大图；支持双指放大 / 双击放大（微信原生能力） */
  async previewImage(e) {
    if (this.data.dragging || this.data.sorting) return;

    const index = Number(e.currentTarget.dataset.index);
    const images = this.data.images;
    if (!images.length) return;

    // 没有旋转过的图就是零成本：直接用原路径
    if (!images.some((item) => item.rotate)) {
      wx.previewImage({
        current: images[index] ? images[index].path : images[0].path,
        urls: images.map((item) => item.path),
      });
      return;
    }

    wx.showLoading({ title: '准备预览…', mask: true });
    try {
      // 并发 2：旋转是高分辨率操作，一次开太多会爆内存
      const urls = await mapLimit(images, 2, (item) => this.previewFile(item));
      wx.hideLoading();
      wx.previewImage({ current: urls[index] || urls[0], urls });
    } catch (err) {
      wx.hideLoading();
      this.showError(err);
    }
  },

  /** 切换排版模式 */
  onPageModeChange(e) {
    if (this.data.generating) return;

    const mode = e.currentTarget.dataset.mode;
    if (mode !== 'fit' && mode !== 'split') return;
    this.setData({ pageMode: mode });
  },

  /* =====================================================================
   *  二、排序（长按进入 → 拖动 / 左右按钮）
   * ===================================================================== */

  /** 长按缩略图进入排序模式 */
  onCellLongPress(e) {
    if (this.data.generating) return;
    if (this.data.images.length < 2) return;

    this.setData({ sorting: true });
    vibrate('medium');
    wx.showToast({ title: '按住图片拖动即可排序', icon: 'none', duration: 1600 });
  },

  /** 退出排序模式 */
  exitSort() {
    this.endDrag();
    this.setData({ sorting: false });
  },

  /** 排序模式下按住某张图，开始拖动 */
  onCellTouchStart(e) {
    if (!this.data.sorting || this.data.generating || this.data.dragging) return;

    const touch = e.touches && e.touches[0];
    const id = e.currentTarget.dataset.id;
    if (!touch || !id) return;

    const index = Number(e.currentTarget.dataset.index);
    this._dragId = id;
    this._dragPageY = touch.pageY;
    this._dragScrollTop = this._scrollTop || 0;

    // 量取所有缩略图的屏幕位置：拖动过程中靠它们判断“应该插到第几位”
    const query = wx.createSelectorQuery();
    query.selectAll('.cell--pic').boundingClientRect();
    query.select('.page').boundingClientRect();
    query.exec((res) => {
      const rects = (res && res[0]) || [];
      const pageRect = (res && res[1]) || null;
      const self = rects[index];
      if (!self || !rects.length) {
        this._dragId = '';
        return;
      }

      // 按“槽位”算位置，而不是直接用量到的矩形：
      // 拖动时那一格脱离文档流，其余格子会向前补位，量到的矩形马上就过期了；
      // 而网格槽位本身不随顺序变化，按公式推算就能得到一个稳定不抖的命中区。
      const cellW = self.width;
      const cellH = self.height;
      const other = rects[rects.length > 1 ? 1 : 0];
      const gapX = rects.length > 1 ? other.left - rects[0].left - cellW : 0;
      const gapY = rects.length > 1 ? other.top - rects[0].top - cellH : 0;

      // 每行几个：从第 0 个往右数，top 相同就是同一行
      let perRow = 1;
      while (perRow < rects.length && Math.abs(rects[perRow].top - rects[0].top) < 1) perRow += 1;

      this._rects = rects.map((_, i) => ({
        left: rects[0].left + (i % perRow) * (cellW + gapX),
        top: rects[0].top + Math.floor(i / perRow) * (cellH + gapY),
        width: cellW,
        height: cellH,
      }));
      this._maxScrollTop = pageRect ? Math.max(0, pageRect.height - windowHeight()) : 0;
      this._dragOffsetX = touch.pageX - self.left;
      this._dragOffsetY = touch.pageY - this._dragScrollTop - self.top;

      this.setData({
        dragging: true,
        dragId: id,
        dragLeft: self.left,
        dragTop: self.top,
        cellW,
        cellH,
      });
      vibrate('light');
    });
  },

  /** 拖动中：跟手 + 就近换位 + 边缘自动滚动 */
  onDragMove(e) {
    if (!this.data.dragging || !this._rects) return;

    const touch = e.touches && e.touches[0];
    if (!touch) return;
    this._dragPageY = touch.pageY;

    const clientX = touch.pageX;
    const clientY = touch.pageY - this._dragScrollTop;

    this.setData({
      dragLeft: clientX - this._dragOffsetX,
      dragTop: clientY - this._dragOffsetY,
    });

    // 就近插入：取中心点离手指最近的格子（比“必须落在格子里”宽容，不会漏判）
    let target = -1;
    let best = Infinity;
    this._rects.forEach((rect, i) => {
      const dx = clientX - (rect.left + rect.width / 2);
      const dy = clientY - (rect.top + rect.height / 2);
      const distance = dx * dx + dy * dy;
      if (distance < best) {
        best = distance;
        target = i;
      }
    });
    if (target >= 0) this.moveTo(target);

    // 拖到上下边缘时自动滚动，否则排在屏幕外的图根本拖不到
    const height = windowHeight();
    if (clientY < EDGE_SCROLL_ZONE) this.autoScrollBy(-EDGE_SCROLL_STEP);
    else if (height && clientY > height - EDGE_SCROLL_ZONE) this.autoScrollBy(EDGE_SCROLL_STEP);
  },

  /** 松手 / 触摸被系统打断 */
  endDrag() {
    if (!this.data.dragging) return;
    this._rects = null;
    this._dragId = '';
    // 拖动期间 onPageScroll 只更新 _dragScrollTop，这里同步回去，
    // 否则下一次长按拖动会拿着旧的滚动值算偏移
    this._scrollTop = this._dragScrollTop || this._scrollTop;
    this.setData({ dragging: false, dragId: '' });
  },

  /** 把正在拖动的图插到 target 位置 */
  moveTo(target) {
    const images = this.data.images;
    const from = images.findIndex((item) => item.id === this._dragId);
    if (from < 0 || target < 0 || target >= images.length || from === target) return;

    const next = images.slice();
    const [moved] = next.splice(from, 1);
    next.splice(target, 0, moved);
    this.setData({ images: next });
    vibrate('light');
  },

  /** 拖到边缘时滚动列表；视口坐标下的矩形要同步平移，否则命中判断会漂 */
  autoScrollBy(delta) {
    const max = this._maxScrollTop || 0;
    const current = this._dragScrollTop || 0;
    const next = Math.max(0, Math.min(max, current + delta));
    const applied = next - current;
    if (!applied) return;

    this._dragScrollTop = next;
    this._rects.forEach((rect) => {
      rect.top -= applied;
      rect.bottom -= applied;
    });
    wx.pageScrollTo({ scrollTop: next, duration: 0, fail: () => {} });
    this.setData({ dragTop: this._dragPageY - next - this._dragOffsetY });
  },

  /** 拖动不好用时的兜底：点 ‹ / › 与相邻一张交换位置 */
  moveImage(e) {
    if (this.data.generating) return;

    const index = Number(e.currentTarget.dataset.index);
    const delta = Number(e.currentTarget.dataset.delta);
    const images = this.data.images;
    const target = index + delta;
    if (index < 0 || index >= images.length || target < 0 || target >= images.length) return;

    const next = images.slice();
    const tmp = next[index];
    next[index] = next[target];
    next[target] = tmp;
    this.setData({ images: next });
    vibrate('light');
  },

  /* =====================================================================
   *  三、生成 PDF 并预览
   * ===================================================================== */

  /** 【生成 PDF 并预览】主流程 */
  async generatePdf() {
    if (this.data.generating) return;

    const images = this.data.images;
    if (!images.length) {
      wx.showToast({ title: '请先添加图片', icon: 'none' });
      return;
    }

    this.setData({ generating: true, sorting: false, dragging: false, progressText: '准备中…' });
    wx.showLoading({ title: '准备中…', mask: true });

    let result;
    try {
      // 1) 生成（分张上传合并 / 一次性 base64）
      result = STRATEGY === 'base64'
        ? await this.convertByBase64(images)
        : await this.convertByUploadFile(images);

      const pdfUrl = result && result.pdf_url;
      if (!pdfUrl) throw new Error('服务端未返回下载地址');

      // 2) 下载 + 打开预览（微信内置阅读器自带双指缩放）
      this.setData({ progressText: '正在下载…' });
      wx.showLoading({ title: '正在下载…', mask: true });
      await this.downloadAndPreview(pdfUrl);
    } catch (err) {
      wx.hideLoading();
      this.setData({ generating: false, progressText: '' });
      this.showError(err);
      return;
    }

    // 成功：先收掉 loading，再弹 toast（两者共用同一个提示通道，顺序不能反）
    wx.hideLoading();
    this.setData({ generating: false, progressText: '' });
    wx.showToast({
      title: result.page_count ? `已生成 ${result.page_count} 页` : '生成成功',
      icon: 'success',
    });
  },

  /* ---------------------- 方式一：分张上传 → 合并 ---------------------- */

  /**
   * 并发上传（限流 3）收集 image_id，最后一次请求按顺序合并成单个 PDF。
   * 旋转优先在客户端完成（所见即所得，也不给服务端添负担）；某张旋转失败时
   * 退回让服务端旋转（旧版服务端会忽略 items 字段，PDF 仍能生成，只是没转）。
   */
  async convertByUploadFile(images) {
    const total = images.length;
    let done = 0;
    const tick = () => {
      done += 1;
      const label = `上传中 ${done}/${total}`;
      this.setData({ progressText: label });
      wx.showLoading({ title: label, mask: true });
    };

    const serverRotations = [];
    const imageIds = await mapLimit(images, UPLOAD_CONCURRENCY, async (item, index) => {
      let path = item.path;
      let rotate = item.rotate || 0;
      try {
        path = await this.prepareFile(item);
        rotate = 0; // 客户端已经转过
      } catch (err) {
        console.warn('[AI拍纸立得] 客户端旋转失败，改由服务端旋转', err);
        path = await this.compress(item.path);
      }

      const uploaded = await this.uploadOne(path);
      serverRotations[index] = rotate;
      tick();
      return uploaded.image_id;
    });

    this.setData({ progressText: '正在合成…' });
    wx.showLoading({ title: '正在合成…', mask: true });

    // 数组顺序即页面顺序，服务端按此顺序排版
    const payload = {
      image_ids: imageIds,
      page_mode: this.data.pageMode,
      pdf_title: PDF_TITLE,
    };
    if (serverRotations.some((rotate) => rotate)) {
      payload.items = imageIds.map((imageId, index) => ({
        image_id: imageId,
        rotate: serverRotations[index] || 0,
      }));
    }
    return this.postJSON(API.convertByIds, payload);
  },

  /** 上传单张图片 → { image_id } */
  uploadOne(filePath) {
    return new Promise((resolve, reject) => {
      wx.uploadFile({
        url: API.uploadOne,
        filePath,
        name: 'file', // 服务端字段名：file
        // 不要手动设置 content-type，微信会自动带 boundary
        timeout: UPLOAD_TIMEOUT,
        success: (res) => {
          const body = this.parseBody(res.data);
          if (res.statusCode >= 200 && res.statusCode < 300 && body && body.image_id) {
            resolve(body);
            return;
          }
          reject(this.buildError(body, `上传失败（HTTP ${res.statusCode}）`));
        },
        fail: (err) => reject(this.buildError(err, '上传失败，请检查网络')),
      });
    });
  },

  /* ---------------------- 方式二：一次性 base64 ---------------------- */

  async convertByBase64(images) {
    const total = images.length;
    let done = 0;
    const payloadImages = await mapLimit(images, UPLOAD_CONCURRENCY, async (item, index) => {
      let path = item.path;
      try {
        path = await this.prepareFile(item);
      } catch (err) {
        console.warn('[AI拍纸立得] 客户端旋转失败，按原图提交', err);
        path = await this.compress(item.path);
      }

      done += 1;
      const label = `读取中 ${done}/${total}`;
      this.setData({ progressText: label });
      wx.showLoading({ title: label, mask: true });

      const base64 = await this.readAsBase64(path);
      return { file_name: `page-${index + 1}.jpg`, data: base64 };
    });

    this.setData({ progressText: '正在合成…' });
    wx.showLoading({ title: '正在合成…', mask: true });

    return this.postJSON(API.convertBase64, {
      images: payloadImages,
      page_mode: this.data.pageMode,
      pdf_title: PDF_TITLE,
    });
  },

  /* ---------------------- 下载 + 预览 ---------------------- */

  /** wx.downloadFile 下载，再 wx.openDocument 打开（允许保存/转发，可双指缩放） */
  async downloadAndPreview(pdfUrl) {
    const filePath = await new Promise((resolve, reject) => {
      wx.downloadFile({
        url: pdfUrl,
        timeout: REQUEST_TIMEOUT,
        success: (res) => {
          if (res.statusCode === 200 && res.tempFilePath) resolve(res.tempFilePath);
          else reject(new Error(`PDF 下载失败（HTTP ${res.statusCode}）`));
        },
        fail: (err) => reject(this.buildError(err, 'PDF 下载失败')),
      });
    });

    // showMenu: true → 右上角菜单可「转发」「保存到手机」「用其他应用打开」
    await new Promise((resolve, reject) => {
      wx.openDocument({
        filePath,
        fileType: 'pdf',
        showMenu: true,
        success: resolve,
        fail: (err) => reject(this.buildError(err, '打开文档失败')),
      });
    });
  },

  /* =====================================================================
   *  四、图片处理（压缩 / 旋转）
   * ===================================================================== */

  /**
   * 得到「最终要上传的那张图」：先压到 1600px，再按需旋转。
   * 结果按 id + 角度缓存，同一张图不会在上传/预览里被反复处理。
   */
  async prepareFile(item) {
    const rotate = item.rotate || 0;
    const cached = this._processed[item.id];
    if (cached && cached.rotate === rotate) return cached.path;

    const compressed = await this.compress(item.path);
    const path = await this.rotateFile(compressed, rotate);
    this._processed[item.id] = { rotate, path };
    return path;
  },

  /** 预览用：已处理过的直接复用，否则用更高分辨率（2400px）旋转一次 */
  async previewFile(item) {
    const rotate = item.rotate || 0;
    if (!rotate) return item.path;

    const processed = this._processed[item.id];
    if (processed && processed.rotate === rotate) return processed.path;

    const key = `${item.id}:${rotate}`;
    if (this._rotatedPreview[key]) return this._rotatedPreview[key];

    const compressed = await this.compress(item.path, PREVIEW_MAX_SIDE);
    const path = await this.rotateFile(compressed, rotate);
    this._rotatedPreview[key] = path;
    return path;
  },

  /** 压缩图片；失败时退回原图，绝不阻断流程 */
  compress(filePath, maxWidth = COMPRESS_MAX_WIDTH) {
    return new Promise((resolve) => {
      wx.compressImage({
        src: filePath,
        quality: COMPRESS_QUALITY,
        compressedWidth: maxWidth,
        success: (res) => resolve(res.tempFilePath || filePath),
        fail: () => resolve(filePath),
      });
    });
  },

  /** 取隐藏 canvas 节点（type="2d"），只查询一次 */
  getCanvasNode() {
    if (this._canvasNode) return Promise.resolve(this._canvasNode);
    if (this._canvasPromise) return this._canvasPromise;

    this._canvasPromise = new Promise((resolve, reject) => {
      wx.createSelectorQuery()
        .select('#rotate-canvas')
        .fields({ node: true, size: true })
        .exec((res) => {
          const node = res && res[0] && res[0].node;
          if (!node) {
            this._canvasPromise = null;
            reject(new Error('旋转组件未就绪，请稍后重试'));
            return;
          }
          this._canvasNode = node;
          resolve(node);
        });
    });
    return this._canvasPromise;
  },

  /**
   * 用隐藏 canvas 把图片顺时针旋转 rotate 度，返回新的临时文件路径。
   *
   * 放在客户端做的原因：缩略图能用 CSS 立刻跟随（所见即所得），
   * 也不用为了转个方向把原图重新上传一遍。
   */
  async rotateFile(filePath, rotate) {
    const degree = (((rotate || 0) % 360) + 360) % 360;
    if (!degree) return filePath;

    const canvas = await this.getCanvasNode();

    const info = await new Promise((resolve, reject) => {
      wx.getImageInfo({
        src: filePath,
        success: resolve,
        fail: () => reject(new Error('读取图片尺寸失败，无法旋转')),
      });
    });

    const { width, height } = info;
    // 极端长图（如 1080x12000 的长截图）直接建同等大小的画布会超过部分机型的
    // canvas 上限（导出空白/报错），所以先算一个缩放比，够清晰又不爆内存
    const fit = Math.min(1, Math.sqrt(MAX_CANVAS_PIXELS / (width * height)));
    const drawW = Math.max(1, Math.round(width * fit));
    const drawH = Math.max(1, Math.round(height * fit));
    const swap = degree % 180 !== 0; // 转 90/270 时长宽互换
    const outW = swap ? drawH : drawW;
    const outH = swap ? drawW : drawH;

    // type="2d" 的 canvas 必须显式设置像素尺寸：
    // CSS 尺寸只影响显示，不设 canvas.width 会按 CSS 尺寸导出而变糊
    canvas.width = outW;
    canvas.height = outH;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, outW, outH);

    const image = canvas.createImage();
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(new Error('图片解码失败，无法旋转'));
      image.src = filePath;
    });

    ctx.save();
    ctx.translate(outW / 2, outH / 2);
    ctx.rotate((degree * Math.PI) / 180); // canvas 的 rotate 是顺时针
    ctx.drawImage(image, -drawW / 2, -drawH / 2, drawW, drawH);
    ctx.restore();

    const res = await new Promise((resolve, reject) => {
      wx.canvasToTempFilePath({
        canvas,
        x: 0,
        y: 0,
        width: outW,
        height: outH,
        destWidth: outW,
        destHeight: outH,
        fileType: 'jpg',
        quality: 0.92,
        success: resolve,
        fail: (err) => reject(this.buildError(err, '旋转导出失败')),
      });
    });
    return res.tempFilePath;
  },

  /* =====================================================================
   *  五、通用工具
   * ===================================================================== */

  /** 读文件为 base64（带 data URL 前缀，后端两种写法都支持） */
  readAsBase64(filePath) {
    return new Promise((resolve, reject) => {
      wx.getFileSystemManager().readFile({
        filePath,
        encoding: 'base64',
        success: (res) => resolve(`data:image/jpeg;base64,${res.data}`),
        fail: (err) => reject(this.buildError(err, '读取图片失败')),
      });
    });
  },

  /** 统一的 JSON POST */
  postJSON(url, data) {
    return new Promise((resolve, reject) => {
      wx.request({
        url,
        method: 'POST',
        data,
        header: { 'content-type': 'application/json' },
        timeout: REQUEST_TIMEOUT,
        success: (res) => {
          const body = res.data || {};
          if (res.statusCode >= 200 && res.statusCode < 300 && body.code === 0) {
            resolve(body);
            return;
          }
          reject(this.buildError(body, `请求失败（HTTP ${res.statusCode}）`));
        },
        fail: (err) => reject(this.buildError(err, '网络异常，请稍后重试')),
      });
    });
  },

  /** wx.uploadFile 返回的 data 是字符串，需要手动 JSON.parse */
  parseBody(raw) {
    if (typeof raw !== 'string') return raw || null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  },

  /** 优先使用后端统一错误结构里的 message */
  buildError(payload, fallback) {
    const message =
      (payload && (payload.message || payload.errMsg)) ||
      (payload && payload.data && payload.data.message) ||
      fallback;
    const error = new Error(message);
    error.code = (payload && payload.code) || 'CLIENT_ERROR';
    return error;
  },

  /** 用户取消操作（选图弹窗、取消下载等）不应弹错误提示 */
  isCancel(err) {
    const msg = (err && (err.errMsg || err.message)) || '';
    return /cancel/i.test(msg);
  },

  /** 错误提示：业务错误码给更具体的引导 */
  showError(err) {
    const code = err && err.code;
    const hints = {
      IMAGE_NOT_FOUND: '图片已过期，请重新添加后再试',
      IMAGE_TOO_SMALL: '有图片分辨率过低，请换一张清晰的',
      UNSUPPORTED_TYPE: '存在不支持的图片格式，请用 JPG/PNG',
      TOO_MANY_FILES: `最多 ${this.data.maxCount || MAX_COUNT} 张图片`,
      FILE_TOO_LARGE: '图片过大，请压缩后再试',
      TOTAL_TOO_LARGE: '图片总大小超限，请减少张数',
      REQUEST_TOO_LARGE: '上传内容过大，请减少张数或压缩图片',
      STORAGE_NOT_WRITABLE: '服务器存储目录不可写，请联系管理员检查挂载权限',
    };

    let content = hints[code] || (err && err.message) || '请稍后重试';

    // 域名白名单：开发者工具/真机调试里勾了「不校验合法域名」就能跑，
    // 但【预览】一定校验 —— 手机上最常见的坑，直接给出该去后台填什么。
    const host = BASE_URL.replace(/^https?:\/\//, '');
    if (/uploadFile:fail|not in domain list|不在以下 .*(uploadFile|downloadFile|request)/i.test(content)) {
      const kind = /uploadFile/i.test(content)
        ? '上传合法域名（uploadFile）'
        : /downloadFile/i.test(content)
          ? '下载合法域名（downloadFile）'
          : 'request 合法域名';
      content =
        `手机上会校验域名白名单（缺：${kind}），开发者工具里的「不校验合法域名」对【预览】不生效。\n\n` +
        `本次请求的目标：\n${content}\n` +
        `当前 AppID：${this.data.appId || '（未取到）'}\n` +
        `环境：${ENV}\n\n` +
        '按这三条逐个排除：\n' +
        '1) 公众平台里配的小程序必须就是这个 AppID（用「测试号」会不一致）\n' +
        '2) 开发管理 → 开发设置 → 服务器域名，request / uploadFile / downloadFile 三栏都填 → 保存（需管理员扫码确认）\n' +
        '3) 改完把小程序从手机“最近使用”里删掉，重新扫码预览（域名列表在启动时拉取）\n' +
        `目标域名：https://${host}`;
    } else if (IS_LOCAL && /网络|request:fail|downloadFile:fail/i.test(content)) {
      content = ENV === 'server'
        ? `连不上服务器（${BASE_URL}）\n` +
          `1) 浏览器打开 ${BASE_URL}/health 是否正常\n` +
          '2) 是否勾选「不校验合法域名…」，或域名已加入后台白名单\n' +
          '3) 云服务器安全组是否放行 443'
        : `连不上后端（${BASE_URL}）\n` +
          '1) 后端是否已启动：python main.py\n' +
          '2) 是否勾选「不校验合法域名…」\n' +
          `3) 浏览器打开 ${BASE_URL}/health 试试`;
    }

    console.error('[AI拍纸立得] 生成失败', code, err);

    wx.showModal({
      title: '生成失败',
      content: `${content}${code && !hints[code] ? `\n(${code})` : ''}`,
      showCancel: false,
      confirmText: '知道了',
    });
  },
});
