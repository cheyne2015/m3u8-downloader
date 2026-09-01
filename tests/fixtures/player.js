// 模拟一个典型播放器 JS：URL 以变量形式拼接，静态 HTML 抓不到
(function () {
    var BASE = "https://cdn.example.com/hls/";
    var quality = {
        low: BASE + "144/index.m3u8",
        ultra: "https://cdn.example.com/hls/4k/index.m3u8"
    };
    // 协议相对路径，应被 urljoin 继承页面 scheme
    var backup = '//cdn.example.com/hls/144p/index.m3u8';
    window.__PLAYER__ = { sources: [quality.low, quality.ultra, backup] };
})();
