import { contextBridge, ipcRenderer } from 'electron';
import type { CoreState, PairRequest, PibloBridge, ThemeMode, UpdateStatus } from '../shared/types';

const invoke = <T>(channel: string, ...args: unknown[]) => ipcRenderer.invoke(channel, ...args) as Promise<T>;

const bridge: PibloBridge = {
  platform: process.platform,
  version: process.argv.find((a) => a.startsWith('--piblo-version='))?.slice('--piblo-version='.length) ?? '',
  core: {
    state: () => invoke('core:state'),
    ensure: () => invoke('core:ensure'),
    restart: () => invoke('core:restart'),
    install: () => invoke('core:install'),
    openLogs: () => invoke('core:open-logs'),
    onState(cb) {
      const handler = (_e: unknown, s: CoreState) => cb(s);
      ipcRenderer.on('core:state-changed', handler);
      return () => ipcRenderer.removeListener('core:state-changed', handler);
    },
  },
  api: (method, path, body) => invoke('api:request', method, path, body),
  screen: () => invoke('api:screen'),
  screenPng: () => invoke('api:screen-png'),
  device: { pair: () => invoke('device:pair') },
  update: {
    status: () => invoke('update:status'),
    check: () => invoke('update:check'),
    install: () => invoke('update:install'),
    onChange(cb) {
      const handler = (_e: unknown, s: UpdateStatus) => cb(s);
      ipcRenderer.on('update:changed', handler);
      return () => ipcRenderer.removeListener('update:changed', handler);
    },
  },
  audio: (path, body) => invoke('api:audio', path, body),
  settings: { get: () => invoke('settings:get'), set: (patch) => invoke('settings:set', patch) },
  env: { get: () => invoke('env:get'), set: (key, value) => invoke('env:set', key, value) },
  openExternal: (url) => invoke('shell:open-external', url),
  saveScreenshot: (png) => invoke('screen:save', png),
  onPairingPending(cb) {
    const handler = (_e: unknown, requests: PairRequest[]) => cb(requests);
    ipcRenderer.on('pairing:pending', handler);
    return () => ipcRenderer.removeListener('pairing:pending', handler);
  },
  onPairingOpen(cb) {
    const handler = () => cb();
    ipcRenderer.on('pairing:open', handler);
    return () => ipcRenderer.removeListener('pairing:open', handler);
  },
  onWindowVisible(cb) {
      const handler = (_e: unknown, visible: boolean) => cb(visible);
      ipcRenderer.on('window:visible', handler);
      return () => ipcRenderer.removeListener('window:visible', handler);
    },
    onSettingsThemeChanged(cb) {
      const handler = (_e: unknown, theme: ThemeMode) => cb(theme);
      ipcRenderer.on('settings:theme-changed', handler);
      return () => ipcRenderer.removeListener('settings:theme-changed', handler);
    },
  };

  contextBridge.exposeInMainWorld('piblo', bridge);
