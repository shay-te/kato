import { useEffect, useRef } from 'react';

const LEVEL_CLASS = {
  ERROR: 'error',
  WARNING: 'warn',
  WARN: 'warn',
};

export default function OrchestratorActivityFeed({ history }) {
  const containerRef = useRef(null);

  useEffect(() => {
    const node = containerRef.current;
    if (node) { node.scrollTop = node.scrollHeight; }
  }, [history?.length]);

  const isEmpty = !history || history.length === 0;
  if (isEmpty) {
    return (
      <div className="orchestrator-feed">
        <header className="orchestrator-feed-header">orchestrator activity</header>
        <div className="orchestrator-feed-empty">
          No activity yet. Scan ticks, task transitions, and warnings will
          appear here as kato runs. Pick a task on the left to inspect its
          files and diff.
        </div>
      </div>
    );
  }

  const rows = history.map((entry) => {
    return <FeedRow key={entry.sequence} entry={entry} />;
  });
  return (
    <div className="orchestrator-feed">
      <header className="orchestrator-feed-header">orchestrator activity</header>
      <div className="orchestrator-feed-body" ref={containerRef}>
        {rows}
      </div>
      <footer className="orchestrator-feed-footer">
        Pick a task on the left to inspect its files and diff.
      </footer>
    </div>
  );
}

function FeedRow({ entry }) {
  const levelClass = LEVEL_CLASS[(entry.level || '').toUpperCase()] || '';
  const ts = entry.epoch
    ? new Date(entry.epoch * 1000).toLocaleTimeString()
    : '';
  return (
    <div className={`orchestrator-feed-row ${levelClass}`.trim()}>
      <span className="ts">{ts}</span>
      <span className="lvl">{(entry.level || '').slice(0, 4)}</span>
      <span className="msg">{entry.message}</span>
    </div>
  );
}
