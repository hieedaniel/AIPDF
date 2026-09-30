// app.js
//
// 这里只放全局配置。页面里（pages/index/index.js）也有一份同样的环境开关，
// 若你新增页面，建议统一改成从 globalData 读取。
const ENV = 'server'; // 'local' | 'local-device' | 'server' | 'prod' —— 需与 pages/index/index.js 保持一致

const BASE_URLS = {
  local: 'http://127.0.0.1:8000',
  'local-device': 'http://192.168.1.100:8000', // TODO: 换成电脑的局域网 IP
  server: 'https://aipdf.seveninfo.cn', // 已部署服务器（本地连服务器调试）
  prod: 'https://aipdf.seveninfo.cn', // TODO: 上线前确认正式域名
};

App({
  globalData: {
    env: ENV,
    apiBaseUrl: BASE_URLS[ENV],
    isLocal: ENV !== 'prod',
  },

  onLaunch() {
    console.log('[AI 拍纸立得] 启动，后端 =', this.globalData.apiBaseUrl);
  },

  onError(err) {
    console.error('[AI 拍纸立得] 全局异常', err);
  },
});
