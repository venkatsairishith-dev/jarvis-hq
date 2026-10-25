/**
 * The Preview panel's rules, as plain functions: what the tool server's answers look like, which images and pages may be shown, and
 * how text, markdown and HTML from a file are made safe before they reach the page.
 *
 * Everything a file can contain is treated as hostile here, even though the tool server has already checked the path, the type and
 * the size (jarvis/bridge/output_preview.py): images only as `data:` URLs of four raster types; HTML only after a strict clean-up and
 * only inside a sandboxed frame without scripts or network; links never navigate the window.
 */

export const JARVIS_OUTPUTS_TOOL = 'jarvis::jarvis_outputs'
export const JARVIS_OUTPUT_GET_TOOL = 'jarvis::jarvis_output_get'
export const JARVIS_WANTED_TOOL = 'jarvis::jarvis_preview_wanted'
/** The entry that already opens a made file in its own app (jarvis/plugins/deliverables, `open_output`). */
export const JARVIS_OPEN_OUTPUT_TOOL = 'jarvis::deliverables__open_output'

export const IMAGE_MIMES: ReadonlySet<string> = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp'])
/** Base64 characters per image the panel accepts (about 6.7 MB of bytes; the server's own cap is 4 MB). */
export const MAX_IMAGE_BASE64 = 9_000_000
export const MAX_TEXT_CHARS = 200_000
export const MAX_HTML_CHARS = 1_100_000

export type PreviewKind = 'deck' | 'pdf' | 'image' | 'text' | 'html' | 'document'
export type PreviewView = 'image' | 'pages' | 'text' | 'html' | 'none'

export interface PreviewItem {
  id: string
  title: string
  kind: PreviewKind
  mime: string
  size: number
  modified: number
  passed: boolean | null
  pages: number | null
  preview: string
}

export interface PreviewImage {
  mime: string
  data: string
}

export interface PreviewThumb extends PreviewImage {
  page: number
}

export interface Preview {
  id: string
  title: string
  kind: PreviewKind
  mime: string
  size: number
  view: PreviewView
  openPath: string
  image?: PreviewImage
  page?: number
  pageCount?: number
  thumbs?: PreviewThumb[]
  text?: string
  language?: string
  truncated?: boolean
  html?: string
  message?: string
}

export interface ToolResult {
  structuredContent?: Record<string, unknown>
  content?: Array<Record<string, unknown>>
  isError?: boolean
}

const KINDS = new Set<string>(['deck', 'pdf', 'image', 'text', 'html', 'document'])
const VIEWS = new Set<string>(['image', 'pages', 'text', 'html', 'none'])
const ID_RE = /^[\w-]{1,600}$/
const BASE64_RE = /^[A-Z0-9+/]+={0,2}$/i

/** The object inside a tool result, from the structured part or the JSON text of the first content item. */
export function toolData(result: ToolResult | undefined | null): Record<string, unknown> | undefined {
  if (!result || result.isError)
    return undefined
  let data: unknown = result.structuredContent
  if (!data) {
    const text = result.content?.find(item => item.type === 'text')?.text
    if (typeof text === 'string') {
      try {
        data = JSON.parse(text)
      }
      catch {
        return undefined
      }
    }
  }
  return data && typeof data === 'object' && !Array.isArray(data) ? data as Record<string, unknown> : undefined
}

/** An id the server handed out: URL-safe base64 only. Anything else is never sent back to the server. */
export function isPreviewId(id: unknown): id is string {
  return typeof id === 'string' && ID_RE.test(id)
}

export function oneLine(value: unknown, limit: number): string {
  return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, limit)
}

export function parseOutputs(result: ToolResult | undefined | null): PreviewItem[] {
  const rows = toolData(result)?.items
  if (!Array.isArray(rows))
    return []
  const items: PreviewItem[] = []
  for (const row of rows.slice(0, 50) as Array<Record<string, unknown>>) {
    if (!row || !isPreviewId(row.id) || !KINDS.has(String(row.kind)))
      continue
    items.push({
      id: row.id,
      title: oneLine(row.title, 120) || 'Untitled',
      kind: row.kind as PreviewKind,
      mime: oneLine(row.mime, 100),
      size: Number.isFinite(Number(row.size)) ? Number(row.size) : 0,
      modified: Number.isFinite(Number(row.modified)) ? Number(row.modified) : 0,
      passed: typeof row.passed === 'boolean' ? row.passed : null,
      pages: Number.isInteger(row.pages) ? row.pages as number : null,
      preview: oneLine(row.preview, 20),
    })
  }
  return items
}

export function parseWanted(result: ToolResult | undefined | null): string[] {
  const ids = toolData(result)?.ids
  return Array.isArray(ids) ? ids.filter(isPreviewId).slice(0, 5) : []
}

function cleanImage(value: unknown): PreviewImage | undefined {
  const v = value as { mime?: unknown, data?: unknown } | undefined
  if (!v || typeof v.mime !== 'string' || typeof v.data !== 'string')
    return undefined
  return imageDataUrl(v as PreviewImage) ? { mime: v.mime, data: v.data } : undefined
}

/** One item's preview, or a plain refusal message. */
export function parsePreview(result: ToolResult | undefined | null): { ok: true, preview: Preview } | { ok: false, message: string } {
  if (!result)
    return { ok: false, message: 'No answer from JARVIS.' }
  if (result.isError)
    return { ok: false, message: 'JARVIS could not show that.' }
  const d = toolData(result)
  if (!d)
    return { ok: false, message: 'JARVIS answered in a form I cannot show.' }
  if (d.ok !== true)
    return { ok: false, message: oneLine(d.message, 200) || 'JARVIS could not show that.' }
  if (!isPreviewId(d.id) || !KINDS.has(String(d.kind)) || !VIEWS.has(String(d.view)))
    return { ok: false, message: 'JARVIS answered in a form I cannot show.' }
  const view = d.view as PreviewView
  const preview: Preview = {
    id: d.id,
    title: oneLine(d.title, 120) || 'Untitled',
    kind: d.kind as PreviewKind,
    mime: oneLine(d.mime, 100),
    size: Number(d.size) || 0,
    view,
    openPath: typeof d.open_path === 'string' ? d.open_path.slice(0, 600) : '',
  }
  if (view === 'image' || view === 'pages') {
    preview.image = cleanImage(d.image)
    if (!preview.image)
      return { ok: false, message: 'The picture is not in a form I will show.' }
  }
  if (view === 'pages') {
    preview.page = Math.max(0, Math.trunc(Number(d.page) || 0))
    preview.pageCount = Math.max(1, Math.trunc(Number(d.page_count) || 1))
    if (Array.isArray(d.thumbs)) {
      preview.thumbs = (d.thumbs as Array<Record<string, unknown>>).slice(0, 24).map(t => ({ page: Math.trunc(Number(t?.page) || 0), image: cleanImage(t) })).filter(t => t.image).map(t => ({ page: t.page, ...(t.image as PreviewImage) }))
    }
  }
  if (view === 'text') {
    preview.text = String(d.text ?? '').slice(0, MAX_TEXT_CHARS)
    preview.language = /^[\w+#-]{1,20}$/.test(String(d.language ?? '')) ? String(d.language) : 'text'
    preview.truncated = d.truncated === true
  }
  if (view === 'html')
    preview.html = String(d.html ?? '').slice(0, MAX_HTML_CHARS)
  if (view === 'none')
    preview.message = oneLine(d.message, 200) || 'There is no preview for this one; open it in its app.'
  return { ok: true, preview }
}

/** A `data:` URL for an image, or null: only png, jpeg, gif and webp, only plain base64, only up to the size cap. */
export function imageDataUrl(image: PreviewImage | undefined | null): string | null {
  if (!image || !IMAGE_MIMES.has(image.mime))
    return null
  if (typeof image.data !== 'string' || image.data.length === 0 || image.data.length > MAX_IMAGE_BASE64 || !BASE64_RE.test(image.data))
    return null
  return `data:${image.mime};base64,${image.data}`
}

/** Markdown that shows `text` as a code block: the fence is longer than any run of backticks inside the text. */
export function fenceCode(text: string, language: string): string {
  const longest = Math.max(0, ...(text.match(/`+/g) ?? []).map(run => run.length))
  const fence = '`'.repeat(Math.max(3, longest + 1))
  const lang = /^[\w+#-]{1,20}$/.test(language) ? language : 'text'
  return `${fence}${lang}\n${text.replace(/\n$/, '')}\n${fence}`
}

export function formatSize(bytes: number): string {
  if (bytes < 1024)
    return `${bytes} B`
  if (bytes < 1024 * 1024)
    return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// ---------------------------------------------------------------------------------------------------------------------
// HTML and markdown clean-up
// ---------------------------------------------------------------------------------------------------------------------

/** What the sandboxed frame may load: nothing. Inline styles and `data:` images and fonts only. */
export const HTML_FRAME_CSP = 'default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; font-src data:; base-uri \'none\'; form-action \'none\''

/** The part of DOMPurify this file uses (kept loose so the real DOMPurify and a test double both fit). */
export interface Purifier {
  sanitize: (...args: any[]) => unknown
  addHook: (...args: any[]) => unknown
  removeHook?: (...args: any[]) => unknown
}

const DATA_IMAGE_SRC = /^data:image\/(?:png|jpeg|gif|webp);base64,[A-Z0-9+/=]+$/i

/** Whether an `img` source carries its own bytes (a png, jpeg, gif or webp `data:` URL) rather than naming an address. */
export function isDataImageSrc(value: string): boolean {
  return DATA_IMAGE_SRC.test(value)
}
const FORBIDDEN_TAGS = ['script', 'meta', 'base', 'link', 'iframe', 'frame', 'frameset', 'object', 'embed', 'form', 'input', 'button', 'textarea', 'select', 'option', 'audio', 'video', 'source', 'track', 'canvas', 'dialog', 'template', 'noscript', 'applet', 'portal']
const FORBIDDEN_ATTRS = ['srcset', 'ping', 'target', 'action', 'formaction', 'background', 'poster', 'xlink:href', 'href', 'download', 'rel', 'ping']

/** A style attribute or sheet without anything that would load a resource: `url(...)` other than a `data:` image, and `@import`. */
export function stripCssLoads(css: string): string {
  return css
    .replace(/@import[^;{]*;?/gi, '')
    .replace(/url\(([^)]*)\)/gi, (whole, inner: string) => /^\s*['"]?data:image\//i.test(inner) ? whole : 'none')
    .replace(/expression\s*\(/gi, '(')
    .replace(/image-set\([^)]*\)/gi, 'none')
}

/** Puts this file's clean-up rules on a DOMPurify instance; the returned function takes them off again. */
export function installPreviewHooks(purify: Purifier): () => void {
  const hooks: Array<[string, (node: any, data?: any) => void]> = [
    ['uponSanitizeAttribute', (node: any, data: any) => {
      const name = String(data.attrName).toLowerCase()
      if (name === 'src') {
        // an image may only carry its own bytes
        if (String(node.nodeName).toLowerCase() !== 'img' || !isDataImageSrc(String(data.attrValue)))
          data.keepAttr = false
      }
      else if (name === 'style') {
        data.attrValue = stripCssLoads(String(data.attrValue))
      }
    }],
    ['afterSanitizeAttributes', (node: any) => {
      // an image that lost its src is not left as a broken-image box: it becomes its description
      if (String(node.nodeName).toLowerCase() === 'img' && !isDataImageSrc(String(node.getAttribute?.('src') ?? ''))) {
        const alt = oneLine(node.getAttribute?.('alt'), 120)
        node.replaceWith(node.ownerDocument.createTextNode(alt ? `[image: ${alt}]` : ''))
      }
    }],
    ['afterSanitizeElements', (node: any) => {
      if (String(node.nodeName).toLowerCase() === 'style' && typeof node.textContent === 'string')
        node.textContent = stripCssLoads(node.textContent)
    }],
  ]
  for (const [name, hook] of hooks)
    purify.addHook(name, hook)
  return () => {
    for (const [name] of hooks)
      purify.removeHook?.(name)
  }
}

/** The settings every piece of shown markup is cleaned with (see `cleanPreviewHtml`): tags and attributes that can load or leave. */
export const PREVIEW_PURIFY_CONFIG = {
  FORBID_TAGS: FORBIDDEN_TAGS,
  FORBID_ATTR: FORBIDDEN_ATTRS,
  ALLOW_DATA_ATTR: false,
  ALLOW_ARIA_ATTR: false,
  KEEP_CONTENT: true,
  WHOLE_DOCUMENT: false,
}

/**
 * Clean HTML (the source of a file, or the output of the markdown renderer) for display:
 * no script, meta, base, link, form, frame or embed; no `href` at all (a link is text, not a way out); an `img` keeps its `src` only
 * when it is a `data:` image; no `srcset`; stylesheets and style attributes lose every `url(...)` that is not a `data:` image.
 */
export function cleanPreviewHtml(source: string, purify: Purifier): string {
  const undo = installPreviewHooks(purify)
  try {
    return String(purify.sanitize(String(source), PREVIEW_PURIFY_CONFIG))
  }
  finally {
    undo()
  }
}

const FRAME_BASE_CSS = 'html{color-scheme:light dark}body{margin:0;padding:12px 16px;font:14px/1.5 system-ui,sans-serif;color:#1c1b22;background:#fff;overflow-wrap:anywhere}a{pointer-events:none;color:inherit;text-decoration:underline dotted}img,svg{max-width:100%;height:auto}'

/** The complete document for the frame's `srcdoc`: the policy first, then the cleaned markup. */
export function htmlFrameDocument(source: string, purify: Purifier): string {
  const body = cleanPreviewHtml(source, purify)
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${HTML_FRAME_CSP}"><style>${FRAME_BASE_CSS}</style></head><body>${body}</body></html>`
}

/** The attributes of the frame itself. `sandbox` is empty on purpose: no scripts, no forms, no popups, no same-origin, no navigation. */
export const HTML_FRAME_ATTRS = {
  sandbox: '',
  referrerpolicy: 'no-referrer',
  csp: HTML_FRAME_CSP,
  loading: 'eager',
} as const

/** A click on a link inside shown content: never lets the window navigate. Returns true when it stopped one. */
export function stopLinkNavigation(event: { target: unknown, preventDefault: () => void, stopPropagation: () => void }): boolean {
  const target = event.target as { closest?: (selector: string) => unknown } | null
  if (target && typeof target.closest === 'function' && target.closest('a, area')) {
    event.preventDefault()
    event.stopPropagation()
    return true
  }
  return false
}

/** Dots that show where the owner is in a deck: the index to show after moving `step` from `index` among `count`, clamped. */
export function movedIndex(index: number, step: number, count: number): number {
  if (count <= 0)
    return 0
  return Math.min(count - 1, Math.max(0, index + step))
}
