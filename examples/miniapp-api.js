/**
 * AI 拍纸立得 · 小程序端调用示例
 * ---------------------------------------------------------------
 * 推荐链路：选图 → 压缩 → 读 base64 → 一次 POST → 下载 PDF → 预览
 * 为什么用 base64：wx.uploadFile 一次只能上传一个文件，
 * 而多个文件分多次上传会在服务端产生多个 PDF，无法保证排版顺序。
 *
 * 前置条件：
 *   1. BASE_URL 使用 HTTPS 域名，并已加入小程序后台「request 合法域名」「downloadFile 合法域名」
 *   2. 图片先压缩，单张建议 ≤ 1MB，总请求体尽量 ≤ 5–10MB
 */

const BASE_URL = 'https://yourdomain.com'; // 生产环境请改成你的域名

/**
 * 选图并合成 PDF
 * @param {Object} options
 * @param {number} [options.count=9]      最多选择张数
 * @param {'fit'|'split'} [options.pageMode='fit'] 排版模式
 * @returns {Promise<{pdf_url: string, file_name: string, page_count: number}>}
 */
async function pickImagesAndConvert(options = {}) {
  const { count = 9, pageMode = 'fit' } = options;

  // 1. 选图（拍照 / 相册）
  const media = await wx.chooseMedia({
    count,
    mediaType: ['image'],
    sourceType: ['camera', 'album'],
    sizeType: ['compressed'],
  });

  // 2. 逐张压缩后转 base64（压缩能显著降低请求体体积）
  const images = [];
  for (let i = 0; i < media.tempFiles.length; i += 1) {
    const small = await compress(media.tempFiles[i].tempFilePath);
    const base64 = await readAsBase64(small);
    images.push({ file_name: `page-${i + 1}.jpg`, data: base64 });
  }

  // 3. 一次请求合成，保证顺序 = 选择顺序
  const result = await wx.request({
    url: `${BASE_URL}/api/v1/convert-to-pdf-base64`,
    method: 'POST',
    header: { 'content-type': 'application/json' },
    timeout: 60000,
    data: {
      images,
      page_mode: pageMode,
      pdf_title: 'AI 拍纸立得',
    },
  });

  const body = result.data || {};
  if (result.statusCode !== 200 || body.code !== 0) {
    throw new Error(body.message || `合成失败（HTTP ${result.statusCode}）`);
  }
  return body;
}

/**
 * 备选链路：单张图片走 multipart（字段名 file），适合逐张处理 / 单页扫描
 */
function uploadOneImage(filePath) {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${BASE_URL}/api/v1/convert-to-pdf`,
      filePath,
      name: 'file', // 服务端同时支持 file 与 files 两个字段
      formData: { page_mode: 'fit', pdf_title: 'scan' },
      timeout: 60000,
      success(res) {
        try {
          const body = JSON.parse(res.data);
          if (res.statusCode === 200 && body.code === 0) resolve(body);
          else reject(new Error(body.message || '合成失败'));
        } catch (e) {
          reject(new Error('返回内容解析失败'));
        }
      },
      fail: reject,
    });
  });
}

/** 下载并预览 PDF（小程序内置 PDF 阅读器） */
async function downloadAndPreview(pdfUrl) {
  const res = await wx.downloadFile({ url: pdfUrl, timeout: 60000 });
  if (res.statusCode !== 200) throw new Error('下载 PDF 失败');

  await wx.openDocument({
    filePath: res.tempFilePath,
    fileType: 'pdf',
    showMenu: true, // 允许用户转发 / 保存到手机
  });
}

/* ----------------------------- 工具函数 ----------------------------- */

/** 压缩图片，长边限制在 1440px 内，质量 80 */
function compress(filePath) {
  return new Promise((resolve, reject) => {
    wx.compressImage({
      src: filePath,
      quality: 80,
      compressedWidth: 1440,
      success: (res) => resolve(res.tempFilePath),
      fail: () => resolve(filePath), // 压缩失败就用原图，不阻断主流程
    });
  });
}

/** 读取文件为 base64（不含 data: 前缀，服务端两种都支持） */
function readAsBase64(filePath) {
  return new Promise((resolve, reject) => {
    wx.getFileSystemManager().readFile({
      filePath,
      encoding: 'base64',
      success: (res) => resolve(`data:image/jpeg;base64,${res.data}`),
      fail: reject,
    });
  });
}

/* ------------------------------ 用法 ------------------------------ */

async function onTapConvert() {
  wx.showLoading({ title: '正在合成…', mask: true });
  try {
    const result = await pickImagesAndConvert({ count: 9, pageMode: 'fit' });
    wx.hideLoading();
    await downloadAndPreview(result.pdf_url);
  } catch (err) {
    wx.hideLoading();
    wx.showModal({
      title: '合成失败',
      content: err.message || '请稍后重试',
      showCancel: false,
    });
  }
}

module.exports = {
  pickImagesAndConvert,
  uploadOneImage,
  downloadAndPreview,
  onTapConvert,
};
