import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import tailwindcss from "@tailwindcss/vite";

// LANLink 前端构建配置。
//
// 产物直接写进 ../static，由 lanlinkd 的 /static 与 /assets 路由提供，
// 并随 git 提交进仓库——设备上没有 Node，release 只发 `git ls-files` 的
// 已 tracked 文件（见 tools/release/release_files.py）。
// 陈旧防护见 tools/build_lanlink_web.py：源码 hash 不匹配即报错。
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../static",
    emptyOutDir: false, // 保留 static/ 下的非构建文件
    assetsDir: "assets",
    // 车机浏览器是固定的一版 Chromium，不需要兼容老引擎；
    // 目标放高可以少发 polyfill，包更小、首屏更快（设备是局域网直连，但 CPU 不快）
    target: "es2022",
    sourcemap: false,
    rollupOptions: {
      output: {
        // 文件名带 content hash：配合 /assets 的 immutable 长缓存，
        // 而 index.html 走 no-cache，OTA 换版后不会拿到旧 JS 配新 API
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
  server: {
    // 开发时把 API 代理到真设备，这样本机就能对着真实 params 调 UI
    proxy: {
      "/api": {
        target: process.env.LANLINK_DEVICE || "http://10.223.134.33:8088",
        changeOrigin: true,
      },
    },
  },
});
