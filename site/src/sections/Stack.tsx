const STAGES: { name: string; where: string; parts: [string, string][] }[] = [
  {
    name: 'Capture',
    where: 'Windows PC and iPhone',
    parts: [
      ['Browser extension', 'pages, reading, searches (MV3)'],
      ['Webcam eye tracker', 'EyeTrax and MediaPipe, a head-aware model that learns from clicks'],
      ['Screen reading', 'other apps through Windows OCR'],
      ['Input counts', 'Raw Input, no key identities'],
      ['Phone', 'walks, barometer floors, voice memos, LiDAR scans, vlogs'],
    ],
  },
  {
    name: 'Vault',
    where: 'plain files you own',
    parts: [
      ['Days', 'JSON and JSONL, appended, never rewritten'],
      ['Notes', 'Markdown that Obsidian opens'],
      ['Search', 'SQLite FTS5, int8 vectors, a CJK index'],
      ['Contracts', '12 JSON Schemas, generating TypeScript and Pydantic'],
    ],
  },
  {
    name: 'Understand',
    where: 'on your GPU',
    parts: [
      ['Speech', 'VAD, SenseVoice, Qwen3-ASR, CAM++ speakers, emotion'],
      ['3D', 'Gaussian splats from scans, COLMAP, ALIKED and LightGlue'],
      ['Video', 'Depth Anything 3 camera poses'],
      ['Items', 'Florence-2, SigLIP 2, RapidOCR'],
      ['Memories', 'a photo into a 3D scene'],
    ],
  },
  {
    name: 'Use',
    where: 'one Windows app',
    parts: [
      ['Viewer', 'React and three.js, Spark for splats'],
      ['API', 'FastAPI on loopback, token for changes'],
      ['Desktop', 'Tauri 2, tray, start at sign-in, self-test'],
      ['Agents', 'a read-only MCP server'],
    ],
  },
];

const FACTS: [string, string][] = [
  ['6', 'services, each in its own Python environment'],
  ['12', 'JSON Schema contracts'],
  ['660+', 'Python tests, plus Playwright on the sample vault'],
  ['2', 'languages in speech, English and Mandarin'],
];

export function Stack(): JSX.Element {
  return (
    <section data-section="stack" id="stack" className="stack">
      <div className="wrap">
        <h2 className="title">How it is built.</h2>
        <ol className="pipeline">
          {STAGES.map((s) => (
            <li key={s.name} className="stage-col">
              <h3>
                {s.name}
                <span>{s.where}</span>
              </h3>
              <dl>
                {s.parts.map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ol>
        <dl className="facts">
          {FACTS.map(([n, t]) => (
            <div key={t}>
              <dt>{n}</dt>
              <dd>{t}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
