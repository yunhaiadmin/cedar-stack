/**
 * qrcode 浏览器构建的类型声明（只声明前端实际用到的 toCanvas）。
 * 不直接 import 'qrcode'：官方 @types/qrcode 顶部有
 * /// <reference types="node" />，一旦被加载会把 Node 全局类型拉进
 * app 工程（setTimeout 返回值变 NodeJS.Timeout，污染无关文件）。
 * 子路径 lib/browser.js 没有 @types，用这份本地声明，运行时由 Vite
 * 直接打包该文件（qrcode 的 browser 构建，不含 node 依赖）。
 */
declare module 'qrcode/lib/browser.js' {
  export interface QRCodeToCanvasOptions {
    width?: number
    margin?: number
    scale?: number
  }
  const QRCode: {
    toCanvas(
      canvas: HTMLCanvasElement,
      text: string,
      options?: QRCodeToCanvasOptions,
    ): Promise<void>
  }
  export default QRCode
}
