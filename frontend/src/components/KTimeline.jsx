import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Legend,
} from 'recharts';

const REFIT_COLOR = '#f97316';

function RefitDot(props) {
  const { cx, cy, payload } = props;
  if (!payload.is_refit_triggered) return null;
  return <circle cx={cx} cy={cy} r={6} fill={REFIT_COLOR} stroke="#fff" strokeWidth={2} />;
}

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="tooltip">
      <div className="tooltip-title">{d.local_index}번째 이벤트</div>
      <div style={{ fontSize: 10, color: '#6b7280', marginBottom: 2 }}>
        global id: {d.event_id}
      </div>
      <div>K = <strong>{d.k_count}</strong> clusters</div>
      <div>Noise = {d.noise_count}</div>
      {d.is_refit_triggered ? (
        <div className="refit-badge">⟳ Refit — {d.refit_reason}</div>
      ) : null}
    </div>
  );
}

export default function KTimeline({ timeline, selectedLocalIdx, onSelect }) {
  const refitEvents = timeline.filter(d => d.is_refit_triggered);

  return (
    <div className="panel">
      <h2>K 변화 타임라인</h2>
      <p className="panel-sub">클릭하면 해당 시점으로 이동합니다</p>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart
          data={timeline}
          onClick={e => e?.activePayload && onSelect(e.activePayload[0].payload.local_index)}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#2e303a" />
          <XAxis
            dataKey="local_index"
            tick={{ fontSize: 11 }}
            label={{ value: '이벤트 순서', position: 'insideBottom', offset: -2, fontSize: 11 }}
          />
          <YAxis
            domain={[0, 6]}
            tick={{ fontSize: 11 }}
            label={{ value: 'K', angle: -90, position: 'insideLeft', fontSize: 11 }}
          />
          <Tooltip content={<CustomTooltip />} />
          {refitEvents.map(d => (
            <ReferenceLine
              key={d.event_id}
              x={d.local_index}
              stroke={REFIT_COLOR}
              strokeDasharray="4 2"
              strokeWidth={1.5}
            />
          ))}
          <ReferenceLine x={selectedLocalIdx} stroke="#60a5fa" strokeWidth={2} />
          <Line
            type="stepAfter"
            dataKey="k_count"
            stroke="#a78bfa"
            strokeWidth={2}
            dot={<RefitDot />}
            activeDot={{ r: 5 }}
            name="K (clusters)"
          />
          <Line
            type="monotone"
            dataKey="noise_count"
            stroke="#6b7280"
            strokeWidth={1}
            dot={false}
            strokeDasharray="4 2"
            name="Noise"
          />
          <Legend verticalAlign="top" height={28} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
