export const REPO = 'https://github.com/Zwc-11/DayCube';
export const OWNER = 'https://github.com/Zwc-11';
export const DOCS = `${REPO}/blob/main/docs/architecture.md`;

/** The repository's star count, or null when GitHub's API is unreachable or rate-limited. */
export async function fetchStars(): Promise<number | null> {
  try {
    const r = await fetch('https://api.github.com/repos/Zwc-11/DayCube', { headers: { Accept: 'application/vnd.github+json' } });
    if (!r.ok) return null;
    const j = (await r.json()) as { stargazers_count?: number };
    return typeof j.stargazers_count === 'number' ? j.stargazers_count : null;
  } catch {
    return null;
  }
}

export function hasWebGL(): boolean {
  try {
    const c = document.createElement('canvas');
    return !!(c.getContext('webgl2') || c.getContext('webgl'));
  } catch {
    return false;
  }
}
