/**
 * JARVIS: PixiJS 6 builds its uniform-upload code with `new Function`, which the renderer's Content-Security-Policy
 * (no 'unsafe-eval') forbids; without this the Stage fails with "Current environment does not allow unsafe-eval".
 * `@pixi/unsafe-eval` is PixiJS's own eval-free replacement. Import this file before creating an Application.
 */
import { ShaderSystem } from '@pixi/core'
import { install } from '@pixi/unsafe-eval'

install({ ShaderSystem })
