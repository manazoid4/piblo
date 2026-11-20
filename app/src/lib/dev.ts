// Pure logic and types for Piblo Dev (Today, Checklists, Content Studio, Agents, Builds, Ctrl+K palette).

export interface DevOverview {
  founder_unticked: number;
  failing_pr_checks: number;
  drafts_awaiting_approval: number;
  usage_warnings: number;
}

export interface DevFeature {
  id: string; name: string; summary: string; category: string; source: string;
  dependencies: string; status: string; steps: string[];
  evidence: { who: string | null; when: string | null; note: string };
}

export interface WorkflowTemplate {
  id: string; title: string; agent_kind: string; steps: string[]; checks: string[];
}

export interface DevMemory {
  configured: boolean; headings?: string[]; handoff?: string; updated?: string | null;
  results?: { path: string; line: string }[];
}

export interface DevChecklistItem {
  id: string;
  text: string;
  done: boolean;
  when?: string;
  note?: string | null;
}

export interface DevChecklist {
  id: string;
  items: DevChecklistItem[];
}

export interface DevTarget {
  platform: 'reddit' | 'x' | 'linkedin' | 'instagram';
  subreddit?: string | null;
  post_type?: 'text' | 'link';
  title?: string | null;
  body?: string | null;
}

export interface DevDraft {
  id: string;
  title: string;
  body: string;
  media_path?: string | null;
  link?: string | null;
  targets: DevTarget[];
  status: 'draft' | 'approved' | 'posted';
  created: string;
  approved_at?: string | null;
  posted_at?: string | null;
}

export interface DevShare {
  platform: string;
  subreddit?: string | null;
  url?: string;
  caption?: string;
  media_path?: string | null;
}

export interface UsageWindow {
  used_percent?: number | null;
  resets_at?: string | null;
}

export interface DevUsage {
  claude?: {
    available: boolean;
    accounts?: {
      number?: number | string;
      five_hour?: UsageWindow;
      seven_day?: UsageWindow;
    }[];
  };
  codex?: {
    available: boolean;
    primary?: UsageWindow;
    secondary?: UsageWindow;
  };
  opencode?: { available: boolean };
  hermes?: { available: boolean };
}

export interface DevPrCheck {
  name?: string;
  context?: string;
  state?: string;
  conclusion?: string;
  detailsUrl?: string;
  targetUrl?: string;
}

export interface DevPr {
  number: number;
  title: string;
  headRefName: string;
  url: string;
  statusCheckRollup?: DevPrCheck[] | null;
}

export interface DevGithub {
  available: boolean;
  prs?: DevPr[];
}

export const CHECKLIST_LABELS: Record<string, { title: string; sub: string }> = {
  'founder-today': {
    title: 'Founder today',
    sub: 'High-priority steps to move Piblo forward today.',
  },
  'feature-tests': {
    title: 'Feature tests',
    sub: 'Real-device checks with optional note and timestamp proof.',
  },
  'kickstarter-gates': {
    title: 'Kickstarter gates',
    sub: 'Launch readiness gates before opening the campaign.',
  },
};

export const X_CHAR_LIMIT = 280;

/** Count characters for an X preview and say whether it fits within 280 characters. */
export function xCounter(text: string): { count: number; left: number; over: boolean } {
  const count = Array.from(text || '').length;
  const left = X_CHAR_LIMIT - count;
  return { count, left, over: left < 0 };
}

/** Calm usage meter classification: amber only strictly above 80%. */
export function usageTone(percent: number | null | undefined): 'calm' | 'amber' | 'unknown' {
  if (typeof percent !== 'number' || Number.isNaN(percent)) return 'unknown';
  return percent > 80 ? 'amber' : 'calm';
}

export function clampPercent(percent: number | null | undefined): number {
  if (typeof percent !== 'number' || Number.isNaN(percent)) return 0;
  return Math.max(0, Math.min(100, Math.round(percent)));
}

/** Summarise a PR's statusCheckRollup into a clean state. */
export function prCheckSummary(pr: DevPr): { state: 'ok' | 'bad' | 'pending' | 'none'; label: string; failing: number; total: number } {
  const checks = pr.statusCheckRollup ?? [];
  if (!checks.length) return { state: 'none', label: 'No checks', failing: 0, total: 0 };
  let failing = 0;
  let pending = 0;
  for (const c of checks) {
    const bad =
      c.conclusion === 'FAILURE' ||
      c.conclusion === 'TIMED_OUT' ||
      c.conclusion === 'CANCELLED' ||
      c.conclusion === 'ACTION_REQUIRED' ||
      c.state === 'FAILURE' ||
      c.state === 'ERROR';
    if (bad) failing++;
    else if (!c.conclusion && c.state !== 'SUCCESS') pending++;
  }
  if (failing > 0) return { state: 'bad', label: `${failing} failing`, failing, total: checks.length };
  if (pending > 0) return { state: 'pending', label: `${pending} running`, failing: 0, total: checks.length };
  return { state: 'ok', label: `${checks.length} passing`, failing: 0, total: checks.length };
}

export interface PaletteCommand {
  id: string;
  label: string;
  group: 'Sections' | 'Help' | 'Piblo Dev' | 'Actions';
  keywords: string;
  shortcut?: string;
}

export const PALETTE_COMMANDS: PaletteCommand[] = [
  { id: 'nav:home', label: 'Go to Home', group: 'Sections', keywords: 'home device battery update beam', shortcut: 'Ctrl+1' },
  { id: 'nav:live', label: 'Go to Live View', group: 'Sections', keywords: 'live view screen mirror keyboard screenshot', shortcut: 'Ctrl+2' },
  { id: 'nav:ask', label: 'Go to Ask', group: 'Sections', keywords: 'ask chat voice question', shortcut: 'Ctrl+3' },
  { id: 'nav:memory', label: 'Go to Memory', group: 'Sections', keywords: 'memory dumps notes library search', shortcut: 'Ctrl+4' },
  { id: 'nav:actions', label: 'Go to Actions', group: 'Sections', keywords: 'actions approvals needs focus quiet', shortcut: 'Ctrl+5' },
  { id: 'nav:usage', label: 'Go to Usage', group: 'Sections', keywords: 'usage tokens claude codex openrouter credits' },
  { id: 'nav:settings', label: 'Go to Settings', group: 'Sections', keywords: 'settings pairing privacy keys theme', shortcut: 'Ctrl+6' },
  { id: 'help:tour', label: 'Tour of this page', group: 'Help', keywords: 'help tour guide walkthrough show me how' },
  { id: 'dev:today', label: 'Piblo Dev: Today', group: 'Piblo Dev', keywords: 'dev today overview founder needs now', shortcut: 'Ctrl+7' },
  { id: 'dev:checklists', label: 'Piblo Dev: Checklists', group: 'Piblo Dev', keywords: 'dev checklists founder today feature tests kickstarter gates tick' },
  { id: 'dev:content', label: 'Piblo Dev: Content Studio', group: 'Piblo Dev', keywords: 'dev content studio drafts reddit x twitter linkedin instagram share approve' },
  { id: 'dev:agents', label: 'Piblo Dev: Agents', group: 'Piblo Dev', keywords: 'dev agents usage meters claude codex opencode hermes' },
  { id: 'dev:builds', label: 'Piblo Dev: Builds', group: 'Piblo Dev', keywords: 'dev builds github pull requests checks ci' },
  { id: 'dev:features', label: 'Piblo Dev: Features', group: 'Piblo Dev', keywords: 'feature catalogue categories tests evidence' },
  { id: 'dev:templates', label: 'Piblo Dev: Templates', group: 'Piblo Dev', keywords: 'workflow template task draft' },
];

export function filterCommands(commands: PaletteCommand[], query: string): PaletteCommand[] {
  const q = query.trim().toLowerCase();
  if (!q) return commands;
  const parts = q.split(/\s+/);
  return commands.filter((c) => {
    const hay = `${c.label} ${c.group} ${c.keywords}`.toLowerCase();
    return parts.every((p) => hay.includes(p));
  });
}

export function formatTickedTime(iso?: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
