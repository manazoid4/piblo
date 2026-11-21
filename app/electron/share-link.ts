/** Explicit-click clipboard capture. Never reads clipboard in the background. */
export function extractClipboardShare(input: string): { url: string; text: string; title: string } | null {
  const text = input.trim().slice(0, 50_000);
  const match = text.match(/https?:\/\/[^\s<>"']+/i);
  if (!match) return null;
  const candidate = match[0].replace(/[.,;:!?)\]}]+$/, '');
  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
    if (parsed.username || parsed.password || !parsed.hostname) return null;
    return { url: candidate.slice(0, 2000), text, title: 'Shared from Windows' };
  } catch {
    return null;
  }
}
