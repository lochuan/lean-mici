# LANLink 前端

设备上**没有 Node**，运行时也不需要。设备只拿构建产物
（`../static/`，随 release 一起发）；本目录是开发用源码。

## 仓库里保留什么

| 内容 | 是否进 git | 是否上设备 | 说明 |
| --- | --- | --- | --- |
| `src/`、`tests/` 源码 | 是 | 否 | 我们自己的代码，含 shadcn-vue 复制过来并改造过的 UI 组件 |
| `package.json`、`package-lock.json` | 是 | 否 | 仅作**依赖说明**，用于 `npm ci` 复现依赖树 |
| `vite.config.ts`、`tsconfig.json` | 是 | 否 | 构建配置 |
| `node_modules/`（约 162 MB） | **否** | 否 | 框架与依赖代码，一律不入库，见 `.gitignore` |
| `../static/` 构建产物 | 是 | **是** | 设备实际加载的文件 |

`tools/release/release_files.py` 的 blacklist 里有 `^openpilot/system/lanlinkd/web/`，
所以本目录不会进入设备镜像；`tools/release/test_release_files.py` 会守住这条规则。

## 依赖说明

精确版本见 `package-lock.json`（这是唯一权威）。各依赖的用途：

**运行时**
- `vue` — 框架。

**构建**
- `vite`、`@vitejs/plugin-vue` — 打包；产物直接写到 `../static/`。
- `tailwindcss`、`@tailwindcss/vite` — 样式（v4，配置在 CSS 里，无 JS config）。
- `typescript`、`vue-tsc`、`@types/node` — 类型检查（`npm run build` 前置）。
  TypeScript **锁在 `~5.9`**：`vue-tsc` 3.x 只声明 `typescript >=5.0`，
  于是 npm 会装上 7.x，而 7.x 移除了 `lib/tsc` 入口，`vue-tsc` 直接
  `ERR_PACKAGE_PATH_NOT_EXPORTED` 崩掉。放宽这个版本会让类型检查失效。
- `vitest` — 单测。

**UI**
- `reka-ui` — shadcn-vue 底层的无头原语（可访问性、键盘交互）。
- `class-variance-authority`、`clsx`、`tailwind-merge` — shadcn-vue 约定的
  class 组合工具（`src/lib/utils.ts` 的 `cn()`）。
- `lucide-vue-next` — 图标。
- `tw-animate-css` — 过渡动画。

> shadcn-vue 的组件不是 npm 依赖：CLI 把**源码**复制到 `src/components/ui/`，
> 之后由本项目维护（已按暗黑极客风改造配色与尺寸），因此它们照常入库。

## 开发

```bash
npm ci                      # 按 lockfile 还原依赖
npm run dev                 # 本机起开发服务器，API 代理到真设备
LANLINK_DEVICE=http://<设备IP>:8088 npm run dev
npm test                    # 单测
npm run build               # 类型检查 + 构建到 ../static/
```

## 提交前必做

产物随 git 提交，所以改完前端要把**源码和产物一起**提交：

```bash
git add openpilot/system/lanlinkd/web          # 先暂存源码（指纹按 git 索引算）
tools/build_lanlink_web.py --build             # 构建 + 写 static/.build-hash
git add openpilot/system/lanlinkd/static
```

`tools/build_lanlink_web.py --check` 会比对 `static/.build-hash` 与源码指纹，
不一致即报错；`tools/release/test_release_files.py` 在测试里跑这个检查。

这道防护是必要的：忘记构建不会有任何报错，release 照常打包，
只是设备上跑的还是旧界面。
