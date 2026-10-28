import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * D78 static import-graph test: no module on the orb's path imports the avatar, Live2D, VRM, MMD, Spine, three.js or any WebGL scene.
 *
 * The orb path is everything under `components/orb/`, its store, and the composables it uses, followed through every relative import. A module
 * that reaches the avatar would load its code and its assets in the orb look, which is exactly what this look exists to avoid. The page
 * `pages/index.vue` may name the avatar page only through a dynamic `import()`, so it loads on demand.
 */

const here = dirname(fileURLToPath(import.meta.url))
const renderer = resolve(here, '../..')
const src = resolve(renderer, '..')

const FORBIDDEN = /live2d|stage-ui-three|stage-ui-mmd|stage-ui-spine|stage-ui-tachie|(?:^|\/)pixi|(?:^|[/@-])three(?:$|[/-])|@tresjs|vrm|model-driver-lipsync|components\/scenes|WidgetStage|avatar\/AvatarStage|webgl|babylon|konva/i

const ROOTS = [
  ...readdirSync(here).filter(file => /\.(?:vue|ts)$/.test(file) && !file.endsWith('.test.ts')).map(file => join(here, file)),
  join(renderer, 'stores/settings/jarvis-look.ts'),
  join(renderer, 'composables/use-hidden-mode-effects.ts'),
  join(renderer, 'composables/use-main-window-voice.ts'),
  join(src, 'shared/jarvis-look.ts'),
  join(src, 'shared/orb-layout.ts'),
  join(src, 'shared/eventa/jarvis-look.ts'),
]

function scriptOf(file: string): string {
  const text = readFileSync(file, 'utf8')
  if (!file.endsWith('.vue'))
    return text
  return [...text.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(match => match[1]).join('\n')
}

/** The module of an `import ... from '...'`, `import '...'` or `export ... from '...'` statement, or `undefined` for any other code. */
function specifierOfStatement(statement: string): string | undefined {
  const trimmed = statement.trimStart()
  const isImport = trimmed.startsWith('import ')
  const isExport = trimmed.startsWith('export ')
  if (!isImport && !isExport)
    return undefined
  const quote = trimmed.search(/['"]/)
  if (quote < 0)
    return undefined
  const head = trimmed.slice(0, quote).trimEnd()
  if (isImport && head !== 'import' && !head.endsWith(' from'))
    return undefined
  if (isExport && !head.endsWith(' from'))
    return undefined
  const end = trimmed.indexOf(trimmed[quote]!, quote + 1)
  return end > quote ? trimmed.slice(quote + 1, end) : undefined
}

/** Every module specifier of static imports and exports, and of dynamic `import()`. Comments are removed first. */
function specifiersOf(code: string): { staticOnes: string[], dynamicOnes: string[] } {
  const clean = code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
  // `import type` / `export type` are erased by the build and load nothing, so they are not part of the graph
  const staticOnes = clean
    .split(/\n(?=\s*(?:import|export)\b)/)
    .filter(statement => !/^\s*(?:import|export)\s+type\b/.test(statement))
    .map(specifierOfStatement)
    .filter((specifier): specifier is string => specifier !== undefined)
  const dynamicOnes = [...clean.matchAll(/import\(\s*['"]([^'"]+)['"]\s*\)/g)].map(match => match[1]!)
  return { staticOnes, dynamicOnes }
}

function resolveRelative(from: string, specifier: string): string | undefined {
  const base = resolve(dirname(from), specifier)
  for (const candidate of [base, `${base}.ts`, `${base}.vue`, `${base}.css`, join(base, 'index.ts'), join(base, 'index.vue')]) {
    if (existsSync(candidate) && statSync(candidate).isFile())
      return candidate
  }
  return undefined
}

function walk() {
  const seen = new Map<string, string[]>()
  const problems: string[] = []
  const queue = [...ROOTS]
  while (queue.length) {
    const file = queue.pop()!
    if (seen.has(file) || !/\.(?:vue|ts)$/.test(file))
      continue
    const { staticOnes, dynamicOnes } = specifiersOf(scriptOf(file))
    seen.set(file, [...staticOnes, ...dynamicOnes])
    const rel = file.slice(src.length + 1)
    for (const specifier of [...staticOnes, ...dynamicOnes]) {
      if (FORBIDDEN.test(specifier))
        problems.push(`${rel} imports ${specifier}`)
      if (specifier.startsWith('.')) {
        const target = resolveRelative(file, specifier)
        if (target)
          queue.push(target)
        else if (!/\.(?:css|svg|png)$/.test(specifier))
          problems.push(`${rel}: cannot resolve ${specifier}`)
      }
    }
    for (const specifier of dynamicOnes)
      problems.push(`${rel} loads ${specifier} on demand (the orb path has no dynamic imports)`)
  }
  return { seen, problems }
}

describe('the orb path imports no avatar, Live2D or WebGL code', () => {
  const { seen, problems } = walk()

  it('walks a real graph (the test is not empty)', () => {
    const files = [...seen.keys()].map(file => file.slice(src.length + 1))
    expect(files.length).toBeGreaterThan(12)
    expect(files).toContain('renderer/components/orb/OrbView.vue')
    expect(files).toContain('renderer/components/orb/OrbSpeechHost.vue')
    expect(files).toContain('renderer/components/orb/orb-state.ts')
    expect(files).toContain('renderer/composables/use-main-window-voice.ts')
  })

  it('has no forbidden import, no unresolved import and no dynamic import', () => {
    expect(problems).toEqual([])
  })

  it('the forbidden pattern really catches the avatar packages', () => {
    for (const bad of ['@proj-airi/stage-ui-live2d', '@proj-airi/stage-ui-three', 'three', 'pixi.js', 'pixi-live2d-display', '@proj-airi/stage-ui/components/scenes', '@tresjs/core', '../avatar/AvatarStage.vue', '@pixiv/three-vrm', '@proj-airi/model-driver-lipsync'])
      expect(FORBIDDEN.test(bad), bad).toBe(true)
    for (const good of ['vue', 'pinia', '@proj-airi/stage-ui/stores/voice', '../hud/hud-model', '@proj-airi/pipelines-audio', '@proj-airi/audio/browser'])
      expect(FORBIDDEN.test(good), good).toBe(false)
  })

  it('the orb components do not touch a canvas or a WebGL context', () => {
    for (const file of seen.keys()) {
      if (!file.includes('/components/orb/'))
        continue
      const code = scriptOf(file).replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
      expect(code, file).not.toMatch(/getContext\(|WebGLRenderingContext|OffscreenCanvas|requestAnimationFrame|\bTresCanvas\b/)
    }
  })

  it('has no animation frame loop in its styles either (CSS animations only)', () => {
    const css = readFileSync(join(here, 'orb.css'), 'utf8')
    expect(css).not.toMatch(/requestAnimationFrame/)
    expect(css).toMatch(/prefers-reduced-motion: reduce/)
  })
})

describe('the main page loads the avatar only on demand', () => {
  const page = readFileSync(join(renderer, 'pages/index.vue'), 'utf8')
  const { staticOnes, dynamicOnes } = specifiersOf(scriptOf(join(renderer, 'pages/index.vue')))

  it('imports the avatar page with import(), never statically', () => {
    expect(staticOnes.filter(specifier => /AvatarStage/.test(specifier))).toEqual([])
    expect(dynamicOnes.some(specifier => /AvatarStage/.test(specifier))).toBe(true)
    expect(page).toContain('v-if="look.look === \'avatar\'"')
  })

  it('has no static import of anything that draws an avatar', () => {
    expect(staticOnes.filter(specifier => FORBIDDEN.test(specifier))).toEqual([])
  })

  it('the Live2D core is loaded by script only for windows that draw an avatar', () => {
    const html = readFileSync(join(renderer, 'index.html'), 'utf8')
    expect(html).not.toMatch(/<script[^>]+live2dcubismcore/)
    expect(html).toContain('/boot.ts')
  })
})
