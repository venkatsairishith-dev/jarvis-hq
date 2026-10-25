<script setup lang="ts">
/**
 * JARVIS's Preview panel: shows the things JARVIS made (decks, reports, images, notes, code, HTML) inside the face.
 *
 * Presentational: the state and the tool-server calls live in `usePreviewController` (../composables/use-jarvis-preview.ts) and the
 * safety rules in ../composables/jarvis-preview-logic.ts. What is drawn here:
 *   - images and page images only as `data:` URLs of png, jpeg, gif or webp (anything else is not drawn);
 *   - text, markdown and code through the face's own markdown renderer (shiki highlighting is bundled: no network) and a strict
 *     clean-up that removes every link target and every outside image;
 *   - HTML in an iframe with `sandbox=""` (no scripts, no forms, no popups, no navigation) and a policy of `default-src 'none'`.
 * Clicking a link in shown content does nothing. The panel has no network access of its own and loads nothing.
 *
 * Small windows: the face's default window is only 252 x 336 px, so under 480 px wide the panel becomes an overlay over the whole
 * window (see the media query at the end of the styles): a two-line header, the page image at the full width, a compact pager,
 * the thumbnails as one scrolling strip behind a toggle, and "Open in app" and close always in view. The panel does not resize the
 * window: the only window-bounds call the face has (electronMainWindowSetFullscreenBounds) works only inside the fullscreen mode,
 * and a new privileged call for this is not worth it.
 */
import type { PreviewController } from '../composables/use-jarvis-preview'

import DOMPurify from 'dompurify'

import { useMarkdown } from '@proj-airi/stage-ui/composables/markdown'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  fenceCode,
  formatSize,
  HTML_FRAME_ATTRS,
  htmlFrameDocument,
  imageDataUrl,
  installPreviewHooks,
  PREVIEW_PURIFY_CONFIG,
  stopLinkNavigation,
} from '../composables/jarvis-preview-logic'

const props = defineProps<{ controller: PreviewController }>()
const { items, current, open, loading, error, note, itemIndex, page, pageCount } = props.controller
const { process } = useMarkdown()

const KIND_LABEL: Record<string, string> = { pdf: 'PDF', html: 'HTML', deck: 'Deck', image: 'Image', text: 'Text', document: 'Document' }
const showList = ref(false)
const expanded = ref(false)
// In a small window the thumbnails are a strip that starts hidden when the window is short (the page image needs the room).
const shortWindow = ref(typeof window !== 'undefined' && window.innerHeight < 520)
const thumbsChoice = ref<boolean | null>(null)
const thumbsOpen = computed(() => thumbsChoice.value ?? !shortWindow.value)
function onResize() {
  shortWindow.value = window.innerHeight < 520
}
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => window.removeEventListener('resize', onResize))
const richHtml = ref('')
let richSeq = 0

const imageUrl = computed(() => imageDataUrl(current.value?.image))
const thumbUrls = computed(() => (current.value?.thumbs ?? []).map(t => ({ page: t.page, url: imageDataUrl(t) })).filter(t => t.url))
const frameDoc = computed(() => current.value?.view === 'html' ? htmlFrameDocument(current.value.html ?? '', DOMPurify) : '')
const subKind = computed(() => current.value ? KIND_LABEL[current.value.kind] ?? current.value.kind : '')
const subSize = computed(() => current.value ? formatSize(current.value.size) : '')
const subPage = computed(() => current.value?.view === 'pages' && pageCount.value > 1 ? `page ${page.value + 1} of ${pageCount.value}` : '')
const checks = computed(() => items.value.find(i => i.id === current.value?.id)?.passed ?? null)

// Text and code are turned into markup in one place; a slow answer never replaces a newer one.
watch(() => current.value && current.value.view === 'text' ? current.value : null, async (c) => {
  const mine = ++richSeq
  richHtml.value = ''
  if (!c || c.text === undefined)
    return
  try {
    const source = c.language === 'markdown' ? c.text : fenceCode(c.text, c.language ?? 'text')
    const html = cleanRich(await process(source))
    if (mine === richSeq)
      richHtml.value = html
  }
  catch {
    // the template falls back to the plain text
  }
}, { immediate: true })

/** Everything that reaches `v-html` below goes through DOMPurify with the panel's strict rules (no links, no outside images, no script). */
function cleanRich(markup: string): string {
  const undo = installPreviewHooks(DOMPurify)
  try {
    return String(DOMPurify.sanitize(markup, PREVIEW_PURIFY_CONFIG))
  }
  finally {
    undo()
  }
}

function toggleList() {
  showList.value = !showList.value
  if (showList.value)
    void props.controller.refresh()
}

async function pick(id: string) {
  showList.value = false
  await props.controller.show(id)
}

function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    props.controller.close()
  }
  else if (event.key === 'ArrowRight' && !(event.target instanceof HTMLInputElement)) {
    event.preventDefault()
    void props.controller.next()
  }
  else if (event.key === 'ArrowLeft' && !(event.target instanceof HTMLInputElement)) {
    event.preventDefault()
    void props.controller.previous()
  }
}

function age(modified: number): string {
  if (!modified)
    return ''
  const seconds = Math.max(0, Date.now() / 1000 - modified)
  if (seconds < 90)
    return 'just now'
  if (seconds < 5400)
    return `${Math.round(seconds / 60)} min ago`
  if (seconds < 129_600)
    return `${Math.round(seconds / 3600)} h ago`
  return `${Math.round(seconds / 86_400)} d ago`
}
</script>

<template>
  <div class="pv-root">
    <button
      v-if="!open"
      class="pv-launch"
      type="button"
      title="Show what JARVIS made"
      aria-label="Open the preview panel"
      @click="props.controller.openPanel()"
    >
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M9 20V9" />
      </svg>
    </button>

    <section
      v-else
      class="pv"
      :class="{ 'pv-expanded': expanded }"
      role="dialog"
      aria-label="Preview"
      tabindex="-1"
      @keydown="onKey"
    >
      <header class="pv-head">
        <div class="pv-titles">
          <strong class="pv-title" :title="current?.title">{{ showList ? 'Recent things' : current?.title || 'Preview' }}</strong>
          <span v-if="!showList && current" class="pv-sub">
            {{ subKind }}<span class="pv-sub-size"> · {{ subSize }}</span><template v-if="subPage"> · {{ subPage }}</template>
            <span v-if="checks === true" class="pv-badge pv-ok"><span class="pv-badge-long">checks passed</span><span class="pv-badge-short" aria-hidden="true">&#10003;</span></span>
            <span v-else-if="checks === false" class="pv-badge pv-warn"><span class="pv-badge-long">checks not passed</span><span class="pv-badge-short" aria-hidden="true">!</span></span>
          </span>
        </div>
        <div class="pv-actions">
          <div class="pv-nav">
            <button type="button" aria-label="Previous item" title="Previous item" :disabled="itemIndex <= 0" @click="props.controller.previousItem()">
              &#8249;
            </button>
            <button type="button" aria-label="Next item" title="Next item" :disabled="itemIndex < 0 || itemIndex >= items.length - 1" @click="props.controller.nextItem()">
              &#8250;
            </button>
            <button type="button" :aria-pressed="showList" aria-label="List of recent things" title="List" @click="toggleList">
              &#9776;
            </button>
            <button type="button" class="pv-expand" :aria-pressed="expanded" aria-label="Make larger or smaller" title="Larger" @click="expanded = !expanded">
              &#9974;
            </button>
          </div>
          <button type="button" class="pv-close" aria-label="Close the preview" title="Close" @click="props.controller.close()">
            &#10005;
          </button>
        </div>
      </header>

      <div class="pv-body" :class="{ 'pv-body-fixed': current?.view === 'pages' && !showList }" @click.capture="stopLinkNavigation($event)" @auxclick.capture="stopLinkNavigation($event)">
        <ul v-if="showList" class="pv-list" aria-label="Recent things">
          <li v-for="item in items" :key="item.id">
            <button type="button" class="pv-row" :class="{ 'pv-row-on': item.id === current?.id }" @click="pick(item.id)">
              <span class="pv-row-title">{{ item.title }}</span>
              <span class="pv-row-meta">
                {{ KIND_LABEL[item.kind] ?? item.kind }} · {{ formatSize(item.size) }}<template v-if="age(item.modified)"> · {{ age(item.modified) }}</template>
                <span v-if="item.passed === true" class="pv-badge pv-ok">checks passed</span>
                <span v-else-if="item.passed === false" class="pv-badge pv-warn">checks not passed</span>
              </span>
            </button>
          </li>
          <li v-if="!items.length" class="pv-empty">
            Nothing has been made yet.
          </li>
        </ul>

        <template v-else>
          <p v-if="error" class="pv-error" role="alert">
            {{ error }}
          </p>
          <p v-else-if="loading && !current" class="pv-empty">
            Loading...
          </p>
          <p v-else-if="!current" class="pv-empty">
            Nothing is open. Ask JARVIS to show something, or pick from the list.
          </p>

          <template v-if="current">
            <div v-if="current.view === 'image' && imageUrl" class="pv-image">
              <img :src="imageUrl" :alt="current.title" decoding="async">
            </div>

            <div v-else-if="current.view === 'pages' && imageUrl" class="pv-pages" :class="{ 'pv-thumbs-open': thumbsOpen }">
              <div class="pv-image">
                <img :src="imageUrl" :alt="`${current.title}, page ${page + 1}`" decoding="async">
              </div>
              <div v-if="pageCount > 1" class="pv-pager">
                <button type="button" aria-label="Previous page" :disabled="page <= 0" @click="props.controller.goToPage(page - 1)">
                  &#8249;<span class="pv-pager-word"> Previous</span>
                </button>
                <span class="pv-pager-count">{{ page + 1 }} / {{ pageCount }}</span>
                <button
                  v-if="thumbUrls.length > 1"
                  type="button"
                  class="pv-thumbs-toggle"
                  :aria-pressed="thumbsOpen"
                  aria-label="Show or hide the page thumbnails"
                  title="Thumbnails"
                  @click="thumbsChoice = !thumbsOpen"
                >
                  &#9638;
                </button>
                <button type="button" aria-label="Next page" :disabled="page >= pageCount - 1" @click="props.controller.goToPage(page + 1)">
                  <span class="pv-pager-word">Next </span>&#8250;
                </button>
              </div>
              <div v-if="thumbUrls.length > 1" class="pv-thumbs" role="list">
                <button
                  v-for="t in thumbUrls"
                  :key="t.page"
                  type="button"
                  role="listitem"
                  class="pv-thumb"
                  :class="{ 'pv-thumb-on': t.page === page }"
                  :aria-label="`Go to page ${t.page + 1}`"
                  @click="props.controller.goToPage(t.page)"
                >
                  <img :src="t.url!" alt="" decoding="async"><span>{{ t.page + 1 }}</span>
                </button>
              </div>
            </div>

            <div v-else-if="current.view === 'text'" class="pv-text">
              <div v-if="richHtml" class="pv-rich" v-html="richHtml" />
              <pre v-else class="pv-plain">{{ current.text }}</pre>
              <p v-if="current.truncated" class="pv-note">
                Only the start is shown; open it in its app for the rest.
              </p>
            </div>

            <iframe
              v-else-if="current.view === 'html'"
              class="pv-frame"
              title="HTML preview (no scripts, no network)"
              :srcdoc="frameDoc"
              :sandbox="HTML_FRAME_ATTRS.sandbox"
              :referrerpolicy="HTML_FRAME_ATTRS.referrerpolicy"
              :csp="HTML_FRAME_ATTRS.csp"
            />

            <p v-else class="pv-empty">
              {{ current.message || 'There is no preview for this one; open it in its app.' }}
            </p>
          </template>
        </template>
      </div>

      <footer class="pv-foot">
        <button type="button" class="pv-primary" :disabled="!current?.openPath" @click="props.controller.openInApp()">
          Open in app
        </button>
        <span v-if="note" class="pv-note" role="status">{{ note }}</span>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.pv-root {
  --pv-bg: #ffffff;
  --pv-fg: #1c1b22;
  --pv-dim: #62606e;
  --pv-line: #dcdbe3;
  --pv-soft: #f3f2f8;
  --pv-accent: #4b3fd6;
  --pv-ok: #1d7a43;
  --pv-warn: #a85a00;
  --pv-err: #b3261e;
}
.pv-launch {
  position: fixed;
  left: 12px;
  bottom: 12px;
  z-index: 55;
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid var(--pv-line);
  border-radius: 999px;
  background: var(--pv-bg);
  color: var(--pv-fg);
  opacity: 0.6;
  cursor: pointer;
  transition: opacity 0.15s;
}
.pv-launch:hover,
.pv-launch:focus-visible {
  opacity: 1;
}
.pv {
  position: fixed;
  top: 12px;
  right: 12px;
  bottom: 12px;
  z-index: 60;
  display: flex;
  flex-direction: column;
  width: min(440px, calc(100vw - 24px));
  overflow: hidden;
  border: 1px solid var(--pv-line);
  border-radius: 12px;
  background: var(--pv-bg);
  color: var(--pv-fg);
  font: 13px/1.45 system-ui, sans-serif;
  box-shadow: 0 8px 32px rgb(0 0 0 / 0.28);
}
.pv-expanded {
  left: 12px;
  width: auto;
}
.pv:focus {
  outline: none;
}
.pv-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 10px 8px 14px;
  border-bottom: 1px solid var(--pv-line);
}
.pv-titles {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}
.pv-title {
  overflow: hidden;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pv-sub {
  color: var(--pv-dim);
  font-size: 12px;
}
.pv-actions,
.pv-nav {
  display: flex;
  gap: 2px;
}
.pv-thumbs-toggle,
.pv-badge-short {
  display: none;
}
.pv-actions button,
.pv-pager button {
  flex: none;
  min-width: 28px;
  height: 28px;
  padding: 0 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--pv-fg);
  font-size: 16px;
  cursor: pointer;
}
.pv-pager button {
  font-size: 12px;
  border: 1px solid var(--pv-line);
}
.pv-actions button:hover:not(:disabled),
.pv-pager button:hover:not(:disabled),
.pv-actions button[aria-pressed='true'] {
  background: var(--pv-soft);
}
button:disabled {
  opacity: 0.35;
  cursor: default;
}
button:focus-visible {
  outline: 2px solid var(--pv-accent);
  outline-offset: 1px;
}
.pv-badge {
  display: inline-block;
  margin-left: 6px;
  padding: 0 6px;
  border: 1px solid currentcolor;
  border-radius: 999px;
  font-size: 11px;
}
.pv-ok {
  color: var(--pv-ok);
}
.pv-warn {
  color: var(--pv-warn);
}
.pv-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.pv-body-fixed {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.pv-pages {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}
.pv-pages .pv-image {
  display: block;
  flex: 1;
  min-height: 0;
  overflow: auto;
  text-align: center;
}
.pv-pages .pv-image img {
  max-width: 100%;
  height: auto;
}
.pv-empty,
.pv-error {
  margin: 0;
  padding: 24px 16px;
  color: var(--pv-dim);
  text-align: center;
}
.pv-error {
  color: var(--pv-err);
}
.pv-list {
  margin: 0;
  padding: 6px;
  list-style: none;
}
.pv-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  width: 100%;
  padding: 8px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.pv-row:hover,
.pv-row-on {
  background: var(--pv-soft);
}
.pv-row-title {
  max-width: 100%;
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pv-row-meta {
  color: var(--pv-dim);
  font-size: 12px;
}
.pv-image {
  display: grid;
  place-items: center;
  padding: 10px;
  background: var(--pv-soft);
}
.pv-image img {
  max-width: 100%;
  max-height: 100%;
  border-radius: 4px;
  box-shadow: 0 1px 6px rgb(0 0 0 / 0.2);
}
.pv-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  color: var(--pv-dim);
}
.pv-thumbs {
  display: flex;
  gap: 6px;
  padding: 4px 12px 12px;
  overflow-x: auto;
}
.pv-thumb {
  position: relative;
  flex: none;
  width: 72px;
  padding: 0;
  border: 2px solid var(--pv-line);
  border-radius: 6px;
  background: var(--pv-soft);
  overflow: hidden;
  cursor: pointer;
}
.pv-thumb-on {
  border-color: var(--pv-accent);
}
.pv-thumb img {
  display: block;
  width: 100%;
}
.pv-thumb span {
  position: absolute;
  right: 2px;
  bottom: 2px;
  padding: 0 4px;
  border-radius: 4px;
  background: rgb(0 0 0 / 0.6);
  color: #fff;
  font-size: 10px;
}
.pv-text {
  padding: 12px 16px;
}
.pv-plain {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 12px/1.5 ui-monospace, Menlo, Consolas, monospace;
}
.pv-rich :deep(h1),
.pv-rich :deep(h2),
.pv-rich :deep(h3) {
  margin: 0.9em 0 0.4em;
  line-height: 1.25;
}
.pv-rich :deep(h1) {
  font-size: 1.5em;
}
.pv-rich :deep(h2) {
  font-size: 1.25em;
}
.pv-rich :deep(p),
.pv-rich :deep(ul),
.pv-rich :deep(ol) {
  margin: 0.5em 0;
}
.pv-rich :deep(a) {
  color: var(--pv-accent);
  pointer-events: none;
  text-decoration: underline dotted;
}
.pv-rich :deep(pre) {
  margin: 0.6em 0;
  padding: 10px 12px;
  overflow-x: auto;
  border-radius: 8px;
  font: 12px/1.5 ui-monospace, Menlo, Consolas, monospace;
}
.pv-rich :deep(code) {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 0.92em;
}
.pv-rich :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
}
.pv-rich :deep(th),
.pv-rich :deep(td) {
  padding: 4px 8px;
  border: 1px solid var(--pv-line);
}
.pv-rich :deep(img) {
  max-width: 100%;
}
.pv-frame {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 320px;
  border: 0;
  background: #fff;
}
.pv-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-top: 1px solid var(--pv-line);
}
.pv-primary {
  height: 28px;
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: var(--pv-accent);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}
.pv-note {
  color: var(--pv-dim);
  font-size: 12px;
}

/*
 * A small window (the face's default is 252 x 336 px): the panel is an overlay over the whole window, nothing runs out of it.
 * Header: the title on its own line (one line, an ellipsis only when truly long) with close beside it; below, what it is, the
 * checks as a mark, and the item buttons. The page image takes the full width, the pager is one slim row and the thumbnails
 * are one scrolling strip that the pager's toggle shows or hides. "Open in app" and close stay in view.
 */
@media (max-width: 479px) {
  .pv,
  .pv-expanded {
    inset: 0;
    width: auto;
    border: 0;
    border-radius: 0;
    box-shadow: none;
  }
  .pv-launch {
    left: 8px;
    bottom: 8px;
  }
  .pv-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    column-gap: 6px;
    row-gap: 0;
    padding: 6px 6px 4px 10px;
  }
  .pv-titles,
  .pv-actions {
    display: contents;
  }
  .pv-title {
    grid-area: 1 / 1;
    font-size: 13px;
  }
  .pv-close {
    grid-area: 1 / 2;
  }
  .pv-sub {
    grid-area: 2 / 1;
    overflow: hidden;
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pv-sub-size,
  .pv-expand {
    display: none;
  }
  .pv-nav {
    grid-area: 2 / 2;
  }
  .pv-actions button {
    min-width: 26px;
    height: 26px;
    padding: 0 4px;
  }
  .pv-badge {
    margin-left: 4px;
    padding: 0 5px;
    font-size: 10px;
  }
  /* the words stay for a screen reader; the sighted reader sees the mark */
  .pv-badge-long {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
  .pv-badge-short {
    display: inline;
  }
  .pv-image {
    padding: 4px;
  }
  .pv-pages .pv-image img {
    width: 100%;
    box-shadow: none;
  }
  .pv-pager {
    gap: 6px;
    padding: 4px 8px;
  }
  .pv-pager button {
    min-width: 36px;
    height: 28px;
  }
  .pv-pager-word {
    display: none;
  }
  .pv-pager-count {
    flex: 1;
    text-align: center;
  }
  .pv-pager .pv-thumbs-toggle {
    display: block;
  }
  .pv-thumbs {
    display: none;
    gap: 4px;
    padding: 2px 8px 6px;
    overscroll-behavior-x: contain;
  }
  .pv-thumbs-open .pv-thumbs {
    display: flex;
  }
  .pv-thumb {
    width: 52px;
  }
  .pv-text {
    padding: 8px 10px;
  }
  .pv-frame {
    min-height: 180px;
  }
  .pv-foot {
    gap: 8px;
    padding: 6px 8px;
  }
  .pv-primary {
    flex: none;
    height: 30px;
  }
  .pv-foot .pv-note {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>

<!-- The face marks dark mode with a `dark` class on <html>. Plain (not scoped) so the selectors keep their parent. -->
<style>
html.dark .pv-root {
  --pv-bg: #1d1c24;
  --pv-fg: #ecebf3;
  --pv-dim: #a09eb0;
  --pv-line: #3a3946;
  --pv-soft: #272631;
  --pv-accent: #9d94ff;
  --pv-ok: #6fd69a;
  --pv-warn: #f0b266;
  --pv-err: #ff8d86;
}
/* shiki writes the light colors inline and the dark ones as variables; inline wins unless the rule says !important */
html.dark .pv-root .pv-rich .shiki {
  color: var(--shiki-dark, inherit) !important;
  background-color: var(--shiki-dark-bg, transparent) !important;
}
html.dark .pv-root .pv-rich .shiki span {
  color: var(--shiki-dark, inherit) !important;
  background-color: transparent !important;
}
html.dark .pv-root .pv-primary {
  color: #14131a;
}
</style>
