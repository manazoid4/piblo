PibloCraft is in early access.

I run more than one coding agent at a time. Claude, Codex, Hermes. The hard part was never the agents. It was knowing which one was waiting on me, which one was stuck, and which one had finished.

So I built a workspace for it. PibloCraft opens your agents in split terminal panes, gives them a shared job queue, and shows what needs you.

What it does today:
- Three panes, each one an agent you pick. Claude (two accounts), Codex, Hermes, or one of 20 other providers.
- Type a job in plain words, or ask for a two hour shift. Work runs in separate git worktrees.
- One short status line: who is working, who is waiting for you, what is open.
- A morning report of what actually happened, so you review before anything merges.

It stays quiet unless you ask. A guard blocks the risky commands, though it is a safety net, not a sandbox. Your keys stay on your own PC.

It runs on Windows today. The code is private for now, so there is an early access list.

piblo.co.uk/piblocraft
