import { GitHubButton } from '../components/Chrome';
import { DOCS, OWNER } from '../lib/links';

export function Final({ stars }: { stars: number | null }): JSX.Element {
  return (
    <section data-section="final" id="final" className="final">
      <div className="final-copy wrap">
        <h2 className="final-title">Walk back into your day.</h2>
        <div className="final-ctas">
          <GitHubButton stars={stars} large />
          <a className="btn btn-ghost btn-lg" href={DOCS} target="_blank" rel="noreferrer">
            Read the architecture
          </a>
        </div>
      </div>
      <footer className="wrap footer">
        <span>DayCube is open source under Apache-2.0. The days, people and places on this page are fictional sample data.</span>
        <a href={OWNER} target="_blank" rel="noreferrer">
          Built by @Zwc-11
        </a>
      </footer>
    </section>
  );
}
