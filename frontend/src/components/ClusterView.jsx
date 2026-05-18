import { useMemo } from 'react';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

const DOT_R = 3; // px — change this to resize all dots
const Dot = ({ cx, cy, fill, opacity }) => <circle cx={cx} cy={cy} r={DOT_R} fill={fill} opacity={opacity ?? 1} />;

const PALETTE = ['#a78bfa', '#34d399', '#fb923c', '#60a5fa', '#f472b6', '#facc15'];
const NOISE_COLOR = '#4b5563';

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const { x, y, c } = payload[0].payload;
  return (
    <div className="tooltip">
      <div>x: {x}, y: {y}</div>
      <div>{c === -1 ? 'Noise' : `Cluster ${c}`}</div>
    </div>
  );
}

export default function ClusterView({ points, clusterInfo, eventData }) {
  const grouped = useMemo(() => {
    const g = { [-1]: [] };
    clusterInfo.forEach(c => { g[c.cluster_id] = []; });
    points.forEach(p => (g[p.c] !== undefined ? g[p.c] : g[-1]).push(p));
    return g;
  }, [points, clusterInfo]);

  return (
    <div className="panel">
      <h2>클러스터 시각화</h2>
      {eventData?.is_refit_triggered ? (
        <div className="refit-badge inline">
          ⟳ Refit 발생 — {eventData.refit_reason}
        </div>
      ) : (
        <div className="panel-sub">이 시점에서 refit 없음</div>
      )}

      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2e303a" />
          <XAxis type="number" dataKey="x" name="x" tick={{ fontSize: 10 }} />
          <YAxis type="number" dataKey="y" name="y" tick={{ fontSize: 10 }} />
          <Tooltip content={<CustomTooltip />} />
          <Legend />
          <Scatter name={`Noise (${grouped[-1].length})`} data={grouped[-1]} fill={NOISE_COLOR} opacity={0.4} shape={<Dot />} />
          {clusterInfo.map((c, i) => (
            <Scatter
              key={c.cluster_id}
              name={`C${c.cluster_id} (${c.size})`}
              data={grouped[c.cluster_id] || []}
              fill={PALETTE[i % PALETTE.length]}
              shape={<Dot />}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>

      <div className="cluster-table-wrap">
        <table className="cluster-table">
          <thead>
            <tr><th>클러스터</th><th>크기</th><th>주요 장르</th></tr>
          </thead>
          <tbody>
            {clusterInfo.map((c, i) => (
              <tr key={c.cluster_id}>
                <td>
                  <span className="color-dot" style={{ background: PALETTE[i % PALETTE.length] }} />
                  C{c.cluster_id}
                </td>
                <td>{c.size}</td>
                <td>{c.top_genres.join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
