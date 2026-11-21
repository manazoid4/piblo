import { contextBridge, ipcRenderer } from 'electron';
import type { CoreState, CraftDraft, CraftTaskRecord, PairRequest, PibloBridge, ThemeMode, UpdateStatus } from '../shared/types';

const invoke = <T>(channel: string, ...args: unknown[]) => ipcRenderer.invoke(channel, ...args) as Promise<T>;

const bridge: PibloBridge = {
  ...(process.isMainFrame && process.argv.includes('--craft-terminal-pilot') ? { craftTask: {
    load: () => invoke<CraftTaskRecord>('craft-task:load'),
    save: (draft: CraftDraft) => invoke<void>('craft-task:save', draft),
    models: () => invoke<string[]>('craft-task:models'),
    run: (draft: CraftDraft) => invoke<CraftTaskRecord>('craft-task:run', draft),
    cancel: () => invoke<void>('craft-task:cancel'),
  }, craftVoice: {
    arm: () => invoke('craft-voice:arm'),
    read: () => invoke('craft-voice:read'),
    cancel: () => invoke('craft-voice:cancel'),
  }, craftMission: {
    get: () => invoke<never>('craft-mission:get'),
  }, craftTerminal: {
    open: (cols: number, rows: number) => invoke<{ id: number; data: string; end: number }>('craft:open', cols, rows),
    read: (id: number, offset: number) => invoke<{ data: string; end: number; exited: boolean; truncated: boolean }>('craft:read', id, offset),
    write: (id: number, data: string) => invoke<void>('craft:write', id, data),
    resize: (id: number, cols: number, rows: number) => invoke<void>('craft:resize', id, cols, rows),
    close: (id: number) => invoke<void>('craft:close', id),
  } } : {}),
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
  usage: { get: () => invoke('usage:get'), setKey: (key) => invoke('usage:set-key', key) },
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
