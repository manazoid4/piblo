import { build, Platform, Arch } from 'electron-builder';

await build({
  targets: Platform.WINDOWS.createTarget('nsis', Arch.x64),
  publish: 'never',
  config: {
    appId: 'uk.piblo.app.craft-pilot',
    productName: 'Piblo Craft Pilot',
    directories: { output: 'release/craft-pilot' },
    nsis: {
      artifactName: 'Piblo-Craft-Pilot-Setup.exe',
      oneClick: true,
      perMachine: false,
      allowToChangeInstallationDirectory: false,
      createDesktopShortcut: false,
      createStartMenuShortcut: false,
      runAfterFinish: false,
      shortcutName: 'Piblo Craft Pilot',
    },
  },
});
