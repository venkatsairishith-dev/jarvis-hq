/* eslint-disable no-template-curly-in-string */

import type { Configuration } from 'electron-builder'

import { execSync } from 'node:child_process'

import { isMacOS } from 'std-env'

function hasXcode26OrAbove() {
  if (!isMacOS)
    return false
  try {
    const output = execSync('xcodebuild -version')
      .toString()

      .match(/Xcode (\d+)/)
    if (!output)
      return false
    return Number.parseInt(output[1], 10) >= 26
  }
  catch {
    return false
  }
}

/**
 * Determine whether to use the .icon format for the macOS app icon based on the
 * Xcode version while building.
 * This is friendly to developers whose macOS and/or Xcode versions are below 26.
 */
const useIconFormattedMacAppIcon = hasXcode26OrAbove()
if (!useIconFormattedMacAppIcon) {
  console.warn('[electron-builder/config] Warning: Xcode version is below 26. Using .icns format for macOS app icon.')
}
else {
  // NOTICE: This success-path message intentionally uses stderr via `console.warn`.
  // The artifact metadata CLI imports this config and is used in GitHub Actions
  // command substitution for `GITHUB_ENV`; writing this log to stdout would break
  // machine-readable output such as `BUNDLE_NAME=$(...)`.
  console.warn('[electron-builder/config] Xcode version is 26 or above. Using .icon format for macOS app icon.')
}

export default {
  appId: 'jarvis.face',
  productName: 'JARVIS',
  // JARVIS: no release feed. The face is built from this repository and never updates itself.
  publish: null,
  directories: {
    output: 'dist',
    buildResources: 'build',
  },
  // // For self-publishing, testing, and distribution after modified the code without access to
  // // an Apple Developer account, comment and uncomment the following lines.
  // // Later on when you obtained one, you can set up the necessary certificates and provisioning
  // // profiles to enable these security features.
  // //
  // // https://www.bigbinary.com/blog/code-sign-notorize-mac-desktop-app
  // // https://kilianvalkhof.com/2019/electron/notarizing-your-electron-application/
  // afterSign: async (context) => {
  //   const { electronPlatformName, appOutDir } = context
  //   if (electronPlatformName !== 'darwin')
  //     return
  //   if (env.CI !== 'true') {
  //     console.warn('Skipping notarizing step. Packaging is not running in CI')
  //     return
  //   }

  //   const appName = context.packager.appInfo.productFilename
  //   await notarize({
  //     appPath: `${appOutDir}/${appName}.app`,
  //     teamId: env.APPLE_DEVELOPER_TEAM_ID!,
  //     appleId: env.APPLE_DEVELOPER_APPLE_ID!,
  //     appleIdPassword: env.APPLE_DEVELOPER_APPLE_APP_SPECIFIC_PASSWORD!,
  //   })
  // },
  files: [
    'out/**',
    'resources/**',
    'package.json',
    // NOTICE: Exclude npm `electron` package from app payload.
    // Electron runtime is already provided by the outer app bundle; bundling a nested
    // `node_modules/electron/dist/Electron.app` makes electron-builder deep-sign it and
    // fails on non-code resources (for example `locale.pak`) with timestamp/signing errors.
    '!**/node_modules/electron{,/**}',
    // Workspace hoisting exposes build tools to electron-builder's file walk.
    // The packaged app has no runtime import of these tools.
    '!**/node_modules/@rolldown{,/**}',
    '!**/node_modules/rolldown{,/**}',
    '!**/node_modules/lightningcss{,/**}',
    '!**/node_modules/lightningcss-*{,/**}',
    '!**/node_modules/fsevents{,/**}',
    // Transformers runs inside browser workers in the desktop app. Sharp is its
    // Node-only image backend and has no packaged main-process consumer.
    '!**/node_modules/sharp{,/**}',
    '!**/node_modules/@img/sharp-*{,/**}',
    // uiohook-napi loads the selected prebuild. Its bundled libuiohook C source is
    // only used to build that binary and does not participate in runtime loading.
    '!**/node_modules/uiohook-napi/libuiohook{,/**}',
    '!**/.vscode/*',
    '!src/**/*',
    '!**/node_modules/**/{CHANGELOG.md,README.md,README,readme.md,readme}',
    '!**/node_modules/**/{.turbo,test,src,__tests__,tests,example,examples}',
    '**/node_modules/debug/**/*',
    '**/node_modules/superjson/**/*',
    '!electron.vite.config.{js,ts,mjs,cjs}',
    '!vite.config.{js,ts,mjs,cjs}',
    '!uno.config.{js,ts,mjs,cjs}',
    '!{.eslintcache,eslint.config.ts,.yaml,dev-app-update.yml,CHANGELOG.md,README.md}',
    '!{.env,.env.*,.npmrc,pnpm-lock.yaml}',
    '!{tsconfig.json}',
  ],
  asar: true,
  asarUnpack: [
    '**/*.node',
  ],
  extraResources: [
    {
      from: '../../engines/stage-tamagotchi-godot/out/${os}',
      to: 'godot-stage',
      filter: ['**/*'],
    },
  ],
  extraMetadata: {
    name: 'jarvis.face',
    main: 'out/main/index.js',
    homepage: 'https://github.com/venkatsairishith-dev/jarvis-hq',
    repository: 'https://github.com/venkatsairishith-dev/jarvis-hq',
    license: 'MIT',
  },
  win: {
    executableName: 'jarvis',
  },
  nsis: {
    artifactName: '${productName}-${version}-windows-${arch}-setup.${ext}',
    shortcutName: '${productName}',
    uninstallDisplayName: '${productName}',
    createDesktopShortcut: 'always',
    // JARVIS: uninstalling the face keeps its settings and chat history.
    deleteAppDataOnUninstall: false,
    oneClick: false,
    allowToChangeInstallationDirectory: true,
    runAfterFinish: true,
  },
  mac: {
    entitlements: 'build/entitlements.mac.plist',
    entitlementsInherit: 'build/entitlements.mac.plist',
    extendInfo: {
      NSMicrophoneUsageDescription: 'JARVIS requires microphone access for voice interaction',
      NSSpeechRecognitionUsageDescription: 'JARVIS uses Apple Speech to transcribe voice interactions on this device',
      NSCameraUsageDescription: 'JARVIS requires camera access for vision understanding',
      NSBluetoothAlwaysUsageDescription: 'JARVIS uses Bluetooth to read game controller input',
    },
    // For self-publishing, testing, and distribution after modified the code without access to
    // an Apple Developer account, comment and uncomment the following 4 lines.
    // Later on when you obtained one, you can set up the necessary certificates and provisioning
    // profiles to enable these security features.
    // hardenedRuntime: false,
    hardenedRuntime: true,
    // notarize: false,
    notarize: true,
    executableName: 'jarvis',
    icon: useIconFormattedMacAppIcon ? 'icon.icon' : 'icon.icns',
  },
  dmg: {
    artifactName: '${productName}-${version}-darwin-${arch}.${ext}',
  },
  linux: {
    target: [
      'deb',
      'rpm',
    ],
    category: 'Utility',
    synopsis: 'The face of JARVIS, a personal assistant.',
    description: 'The face of JARVIS: an avatar, chat and voice window. Built from Project AIRI (MIT).',
    executableName: 'jarvis',
    artifactName: '${productName}-${version}-linux-${arch}.${ext}',
    icon: 'build/icons/icon.png',
  },
  appImage: {
    artifactName: '${productName}-${version}-linux-${arch}.${ext}',
  },
  npmRebuild: false,

} satisfies Configuration
