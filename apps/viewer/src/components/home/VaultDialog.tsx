import React from 'react';
import { Database, Network, X } from 'lucide-react';
import { Dialog } from '../ui';
import { DataCenterView, LiveGraph } from '../modals/DataSections';

export type VaultView = 'data' | 'graph';

const VIEWS: { id: VaultView; label: string; icon: React.ReactNode }[] = [
  { id: 'data', label: 'Data center', icon: <Database size={15} /> },
  { id: 'graph', label: 'Knowledge graph', icon: <Network size={15} /> },
];

/** The vault's data center and knowledge graph, reachable from the calendar before any day opens. */
const VaultDialog: React.FC<{ view: VaultView; online: boolean; onView: (v: VaultView) => void; onClose: () => void }> = ({
  view,
  online,
  onView,
  onClose,
}) => (
  <Dialog onClose={onClose} title="Your data" className="settings">
    <div className="dialog__head" style={{ alignItems: 'center' }}>
      <h2 className="dialog__title">Your data</h2>
      <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
        <X size={18} />
      </button>
    </div>
    <div className="settings__body">
      <nav className="settings__nav scroll" aria-label="Data views">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            className="nav-item"
            aria-current={view === v.id ? 'page' : undefined}
            onClick={() => onView(v.id)}
          >
            {v.icon}
            {v.label}
          </button>
        ))}
      </nav>
      <div className="settings__content scroll">
        <h3 className="settings__h">{VIEWS.find((v) => v.id === view)?.label}</h3>
        {view === 'data' ? (
          <DataCenterView online={online} />
        ) : online ? (
          <LiveGraph />
        ) : (
          <div className="empty">Connect to the local API (npm run api) to see your vault's graph.</div>
        )}
      </div>
    </div>
  </Dialog>
);

export default VaultDialog;
