import { ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

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
  const grouped = {};
  grouped[-1] = [];
  clusterInfo.forEach(c => { grouped[c.cluster_id] = []; });

  points.forEach(p => {
    if (grouped[p.c] !== undefined) grouped[p.c].push(p);
    else grouped[-1].push(p);
  });

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
          <ZAxis range={[12, 12]} />
          <Tooltip content={<CustomTooltip />} />
          <Legend />
          <Scatter name={`Noise (${grouped[-1].length})`} data={grouped[-1]} fill={NOISE_COLOR} opacity={0.4} />
          {clusterInfo.map((c, i) => (
            <Scatter
              key={c.cluster_id}
              name={`C${c.cluster_id} (${c.size})`}
              data={grouped[c.cluster_id] || []}
              fill={PALETTE[i % PALETTE.length]}
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
