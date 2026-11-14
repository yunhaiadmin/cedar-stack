<template>
  <main class="licenses-page">
    <div class="licenses-inner">
      <p class="au-eyebrow licenses-eyebrow">Aetrix</p>
      <h1 class="licenses-title">开源许可</h1>
      <p class="licenses-note">
        本站网页播放器在浏览器无法硬解某个编码时，会用本站自编的 LGPL 解码器（WebAssembly）在网页内软解兜底；相关第三方许可说明与 LGPL 全文见下方链接。
      </p>
      <ul class="licenses-list">
        <li>
          <a href="/wasm/THIRD-PARTY-LICENSES.txt" target="_blank" rel="noopener">第三方许可说明</a>
        </li>
        <li>
          <a href="/wasm/COPYING.LGPLv3" target="_blank" rel="noopener">LGPLv3 全文</a>
        </li>
      </ul>
    </div>
  </main>
</template>

<style scoped>
/* 文档面排版与 PrivacyView 同口径（说明书 §2.12 文章卡）：
   缺省深色面、html[data-theme='light'] 换浅，只引用 --uhd-* 令牌。 */
.licenses-page {
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 64px 16px calc(48px + var(--au-dock-space));
  background: var(--uhd-d-bg);
  color: var(--uhd-d-text);
  font-family: var(--uhd-font-sans);
}

.licenses-inner {
  width: 100%;
  max-width: var(--uhd-doc-article-w);
  background: var(--uhd-d-surface);
  border: 1px solid var(--uhd-d-border);
  border-radius: var(--uhd-radius-doc);
  padding: 48px;
}

.licenses-eyebrow {
  margin: 0 0 16px;
  font-size: var(--uhd-fs-aux-sm);
  font-weight: var(--uhd-fw-semibold);
  letter-spacing: var(--uhd-ls-eyebrow);
  text-transform: uppercase;
  color: var(--uhd-d-text-2);
}

.licenses-title {
  margin: 0 0 16px;
  font-size: var(--uhd-fs-h2);
  font-weight: var(--uhd-fw-extrabold);
  line-height: 1.2;
}

.licenses-note {
  margin: 0 0 28px;
  font-size: var(--uhd-fs-lead);
  line-height: var(--uhd-lh-doc);
  color: var(--uhd-d-text-2);
}

.licenses-list {
  margin: 0;
  padding-left: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  font-size: var(--uhd-fs-body);
  line-height: var(--uhd-lh-doc);
}

.licenses-list li::marker {
  color: var(--uhd-d-text-3);
}

.licenses-list a {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.licenses-list a:hover {
  opacity: 0.8;
}

/* 浅色文档面：跟随用户外观（html[data-theme='light']） */
html[data-theme='light'] .licenses-page {
  background: var(--uhd-l-bg);
  color: var(--uhd-l-text);
}

html[data-theme='light'] .licenses-inner {
  background: var(--uhd-l-surface);
  border-color: var(--uhd-l-border);
  box-shadow: var(--uhd-shadow-card-l);
}

html[data-theme='light'] .licenses-eyebrow {
  color: var(--uhd-l-text-2);
}

html[data-theme='light'] .licenses-note {
  color: var(--uhd-l-text-2);
}

html[data-theme='light'] .licenses-list li::marker {
  color: var(--uhd-l-text-3);
}

@media (max-width: 640px) {
  .licenses-page {
    padding: 24px 12px calc(32px + var(--au-dock-space));
  }

  .licenses-inner {
    padding: 28px 20px;
  }

  .licenses-title {
    font-size: 32px;
  }
}
</style>
