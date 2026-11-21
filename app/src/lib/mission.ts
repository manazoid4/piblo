export interface MissionAgent { id: string; label: string; kind?: string; installed: boolean; active: boolean; pct_left: number | null; resets_at?: string | null }
export interface MissionTask { id: string; agent: string; title: string; status: 'waiting' | 'running' | 'done' | 'failed'; progress?: number | null; detail?: string; proof?: string; updated?: string }
export interface MissionData { agents: MissionAgent[]; tasks: MissionTask[]; error?: string }

export const STATUS_LABEL: Record<MissionTask['status'], string> = { waiting: 'Blocked', running: 'Working', done: 'Done', failed: 'Failed' };

/** One line for the top of the card: failed first, then waiting, then running. */
export function nextAction(tasks: MissionTask[]): string {
  for (const [status, verb] of [['failed', 'Fix'], ['waiting', 'Unblock'], ['running', 'Watching']] as const) {
    const t = tasks.find((x) => x.status === status);
    if (t) return `${verb}: ${t.title}`;
  }
  return 'All clear';
}

/** Tasks grouped by agent, in agent order, then any unknown agents. */
export function groupByAgent(agents: MissionAgent[], tasks: MissionTask[]) {
  const ids = [...agents.map((a) => a.id), ...tasks.map((t) => t.agent)];
  return [...new Set(ids)].map((id) => ({ id, agent: agents.find((a) => a.id === id), tasks: tasks.filter((t) => t.agent === id) }));
}

export const agentTone = (a: MissionAgent): 'ok' | 'warn' | 'idle' | 'off' =>
  !a.installed ? 'off' : a.pct_left !== null && a.pct_left < 15 ? 'warn' : a.active ? 'ok' : 'idle';
