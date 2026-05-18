import { useMemo } from 'react';

function Spark({ history, key_ }) {
  const vals = history.map(h => h[key_]).filter(v => v != null);
  if (!vals.length) return null;
  const max = Math.max(...vals, 1);
  const W = 80, H = 28;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1 || 1)) * W},${H - (v / max) * H}`).join(' ');
  return (
    <svg width={W} height={H} style={{ display: 'block' }}>
      <polyline points={pts} fill="none" stroke="#a78bfa" strokeWidth="1.5" />
    </svg>
  );
}

export default function PerfOverlay({ stats, visible, onToggle }) {
  const avgFetch  = useMemo(() => {
    const v = stats.history.map(h => h.fetchMs).filter(Boolean);
    return v.length ? (v.reduce((a, b) => a + b, 0) / v.length).toFixed(1) : '—';
  }, [stats.history]);

  const avgRender = useMemo(() => {
    const v = stats.history.map(h => h.renderMs).filter(Boolean);
    return v.length ? (v.reduce((a, b) => a + b, 0) / v.length).toFixed(1) : '—';
  }, [stats.history]);

  const hitRate = stats.cacheHits + stats.cacheMisses > 0
    ? ((stats.cacheHits / (stats.cacheHits + stats.cacheMisses)) * 100).toFixed(0)
    : '—';

  return (
    <div className="perf-toggle-wrap">
      <button className="perf-toggle" onClick={onToggle}>⏱ Perf</button>
      {visible && (
        <div className="perf-overlay">
          <div className="perf-row">
            <span className="perf-label">Fetch</span>
            <span className="perf-value">{stats.fetchMs ?? '—'} ms</span>
            <span className="perf-avg">(avg {avgFetch} ms)</span>
          </div>
          <Spark history={stats.history} key_="fetchMs" />

          <div className="perf-row" style={{ marginTop: 8 }}>
            <span className="perf-label">Render</span>
            <span className="perf-value">{stats.renderMs ?? '—'} ms</span>
            <span className="perf-avg">(avg {avgRender} ms)</span>
          </div>
          <Spark history={stats.history} key_="renderMs" />

          <div className="perf-row" style={{ marginTop: 8 }}>
            <span className="perf-label">Cache</span>
            <span className="perf-value">{hitRate}% hit</span>
            <span className="perf-avg">({stats.cacheHits}✓ / {stats.cacheMisses}✗)</span>
          </div>
        </div>
      )}
    </div>
  );
}
