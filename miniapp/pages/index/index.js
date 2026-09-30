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
};

// 'uploadfile' = 循环 wx.uploadFile 分张上传后合并（默认）
// 'base64'     = 一次性 base64 JSON（代码最短，但请求体比原图大约 1/3）
const STRATEGY = 'uploadfile';

const MAX_COUNT = 9; // 单次最多张数，需 ≤ 后端 MAX_FILE_COUNT
const UPLOAD_TIMEOUT = 60 * 1000; // 单张上传超时
const REQUEST_TIMEOUT = 60 * 1000; // 合成 / 下载超时
const COMPRESS_QUALITY = 80; // 上传前压缩质量
const COMPRESS_MAX_WIDTH = 1600; // 上传前压缩后的长边像素
const PDF_TITLE = 'AI拍纸立得';

Page({
  data: {
    images: [], // [{ path, size }]，数组顺序 = PDF 排版顺序
    maxCount: MAX_COUNT,
    pageMode: 'fit', // fit=每图一页，split=长图自动分页
    generating: false, // 合成中：禁用所有按钮，避免重复提交
    progressText: '', // 显示在主按钮上的进度文案
    isLocal: IS_LOCAL, // 调试态时在顶部展示当前后端地址
    env: ENV, // 当前环境，便于在 WXML 里做条件渲染
    baseUrl: BASE_URL,
  },

  /* =====================================================================
   *  一、选图 / 管理图片
   * ===================================================================== */

  /** 【添加图片 / 拍照】相册或相机，追加到数组末尾 */
  async chooseImages() {
    if (this.data.generating) return;

    const rest = MAX_COUNT - this.data.images.length;
    if (rest <= 0) {
      wx.showToast({ title: `最多 ${MAX_COUNT} 张`, icon: 'none' });
      return;
    }

    let res;
    try {
      res = await wx.chooseMedia({
        count: rest,
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
      .map((f) => ({ path: f.tempFilePath, size: f.size || 0 }));

    if (!picked.length) return;

    this.setData({ images: this.data.images.concat(picked) });
    wx.vibrateShort({ type: 'light' });
  },

  /** 删除单张（网格上的 ✕） */
  removeImage(e) {
    if (this.data.generating) return;

    const index = Number(e.currentTarget.dataset.index);
    const images = this.data.images.slice();
    if (index < 0 || index >= images.length) return;

    images.splice(index, 1);
    this.setData({ images });
    wx.vibrateShort({ type: 'light' });
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
        if (res.confirm) this.setData({ images: [] });
      },
    });
  },

  /** 点击缩略图看大图，左右可滑动 */
  previewImage(e) {
    const index = Number(e.currentTarget.dataset.index);
    const urls = this.data.images.map((item) => item.path);
    if (!urls.length) return;

    wx.previewImage({ current: urls[index] || urls[0], urls });
  },

  /** 切换排版模式 */
  onPageModeChange(e) {
    if (this.data.generating) return;

    const mode = e.currentTarget.dataset.mode;
    if (mode !== 'fit' && mode !== 'split') return;
    this.setData({ pageMode: mode });
  },

  /* =====================================================================
   *  二、生成 PDF 并预览
   * ===================================================================== */

  /** 【生成 PDF 并预览】主流程 */
  async generatePdf() {
    if (this.data.generating) return;

    const paths = this.data.images.map((item) => item.path);
    if (!paths.length) {
      wx.showToast({ title: '请先添加图片', icon: 'none' });
      return;
    }

    this.setData({ generating: true, progressText: '准备中…' });
    wx.showLoading({ title: '准备中…', mask: true });

    let result;
    try {
      // 1) 生成（分张上传合并 / 一次性 base64）
      result = STRATEGY === 'base64'
        ? await this.convertByBase64(paths)
        : await this.convertByUploadFile(paths);

      const pdfUrl = result && result.pdf_url;
      if (!pdfUrl) throw new Error('服务端未返回下载地址');

      // 2) 下载 + 打开预览
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
   * 循环 wx.uploadFile 逐张上传，收集 image_id，
   * 最后一次请求按顺序合并为单个 PDF。
   */
  async convertByUploadFile(paths) {
    const imageIds = [];

    for (let i = 0; i < paths.length; i += 1) {
      const label = `上传中 ${i + 1}/${paths.length}`;
      this.setData({ progressText: label });
      wx.showLoading({ title: label, mask: true });

      const filePath = await this.compress(paths[i]); // 压缩，控制请求体
      const uploaded = await this.uploadOne(filePath);
      imageIds.push(uploaded.image_id);
    }

    this.setData({ progressText: '正在合成…' });
    wx.showLoading({ title: '正在合成…', mask: true });

    // 数组顺序即页面顺序，服务端按此顺序排版
    return this.postJSON(API.convertByIds, {
      image_ids: imageIds,
      page_mode: this.data.pageMode,
      pdf_title: PDF_TITLE,
    });
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

  async convertByBase64(paths) {
    const images = [];

    for (let i = 0; i < paths.length; i += 1) {
      const label = `读取中 ${i + 1}/${paths.length}`;
      this.setData({ progressText: label });
      wx.showLoading({ title: label, mask: true });

      const filePath = await this.compress(paths[i]);
      const base64 = await this.readAsBase64(filePath);
      images.push({ file_name: `page-${i + 1}.jpg`, data: base64 });
    }

    this.setData({ progressText: '正在合成…' });
    wx.showLoading({ title: '正在合成…', mask: true });

    return this.postJSON(API.convertBase64, {
      images,
      page_mode: this.data.pageMode,
      pdf_title: PDF_TITLE,
    });
  },

  /* ---------------------- 下载 + 预览 ---------------------- */

  /** 依次 wx.downloadFile 下载，再 wx.openDocument 打开（允许保存/转发） */
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
   *  三、通用工具
   * ===================================================================== */

  /** 压缩图片；失败时退回原图，绝不阻断流程 */
  compress(filePath) {
    return new Promise((resolve) => {
      wx.compressImage({
        src: filePath,
        quality: COMPRESS_QUALITY,
        compressedWidth: COMPRESS_MAX_WIDTH,
        success: (res) => resolve(res.tempFilePath || filePath),
        fail: () => resolve(filePath),
      });
    });
  },

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
      TOO_MANY_FILES: `最多 ${MAX_COUNT} 张图片`,
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
        `手机上会校验域名白名单（${kind}），开发者工具里的「不校验合法域名」对【预览】不生效。\n` +
        `去小程序后台配置：开发管理 → 开发设置 → 服务器域名\n` +
        `request / uploadFile / downloadFile 三栏都填：https://${host}\n` +
        '保存后重新预览即可（不用重新上传代码）。';
    } else if (IS_LOCAL && /网络|request:fail|downloadFile:fail/i.test(content)) {
      content = ENV === 'server'
        ? `连不上服务器（${BASE_URL}）\n` +
          `1) 浏览器打开 ${BASE_URL}/health 是否正常\n` +
          '2) 是否勾选「不校验合法域名…」，或域名已加入后台白名单\n' +
          '3) 云服务器安全组是否放行 443'
        : `连不上后端（${BASE_URL}）\n` +
          '1) 后端是否已启动：python main.py\n' +
          '2) 是否勾选「不校验合法域名…」\n' +
          '3) 浏览器打开 ' + BASE_URL + '/health 试试';
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
