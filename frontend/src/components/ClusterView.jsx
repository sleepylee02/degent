import { useMemo } from 'react';
import {
  Chart as ChartJS,
  LinearScale, PointElement, Tooltip, Legend,
} from 'chart.js';
import { Scatter } from 'react-chartjs-2';

ChartJS.register(LinearScale, PointElement, Tooltip, Legend);

const PALETTE = ['#a78bfa', '#34d399', '#fb923c', '#60a5fa', '#f472b6', '#facc15'];
const NOISE_COLOR = '#4b5563';

const OPTIONS = {
  animation: false,
  responsive: true,
  maintainAspectRatio: false,
  parsing: false, // points already {x, y} — skip internal parsing
  plugins: {
    legend: {
      position: 'top',
      labels: { color: '#e2e8f0', boxWidth: 10, font: { size: 11 } },
    },
    tooltip: {
      callbacks: {
        label: ctx => {
          const { x, y, c } = ctx.raw;
          return `(${x?.toFixed(2)}, ${y?.toFixed(2)})  ${c === -1 ? 'Noise' : `C${c}`}`;
        },
      },
    },
  },
  scales: {
    x: { ticks: { color: '#6b7280', font: { size: 10 } }, grid: { color: '#2e303a' } },
    y: { ticks: { color: '#6b7280', font: { size: 10 } }, grid: { color: '#2e303a' } },
  },
};

export default function ClusterView({ points, clusterInfo, eventData }) {
  const data = useMemo(() => {
    const grouped = { [-1]: [] };
    clusterInfo.forEach(c => { grouped[c.cluster_id] = []; });
    points.forEach(p => (grouped[p.c] !== undefined ? grouped[p.c] : grouped[-1]).push(p));

    return {
      datasets: [
        {
          label: `Noise (${grouped[-1].length})`,
          data: grouped[-1],
          backgroundColor: NOISE_COLOR + '66',
          pointRadius: 3,
          pointHoverRadius: 5,
        },
        ...clusterInfo.map((c, i) => ({
          label: `C${c.cluster_id} (${c.size})`,
          data: grouped[c.cluster_id] ?? [],
          backgroundColor: PALETTE[i % PALETTE.length] + 'cc',
          pointRadius: 3,
          pointHoverRadius: 5,
        })),
      ],
    };
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

      <div style={{ height: 300 }}>
        <Scatter data={data} options={OPTIONS} />
      </div>

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
