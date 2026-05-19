import { useState, useEffect, useRef, useCallback, useMemo, memo } from 'react';
import { fetchUserIds, fetchTimeline, fetchFrame, fetchAllVizStates } from './api';
import KTimeline from './components/KTimeline';
import ClusterView from './components/ClusterView';
import Recommendations from './components/Recommendations';
import PerfOverlay from './components/PerfOverlay';
import { usePerf } from './hooks/usePerf';
import './App.css';

const PREFETCH_AHEAD = 3; // number of frames to prefetch during playback

const MemoKTimeline      = memo(KTimeline);
const MemoClusterView    = memo(ClusterView);
const MemoRecommendations = memo(Recommendations);

export default function App() {
  const [userIds, setUserIds]             = useState([]);
  const [userId, setUserId]               = useState(null);
  const [timeline, setTimeline]           = useState([]);
  const [sliderIdx, setSliderIdx]         = useState(0);
  const [appliedIdx, setAppliedIdx]       = useState(0);
  const [frame, setFrame]                 = useState(null);
  const [vizData, setVizData]             = useState({}); // event_id → {checkpoint_id, points_data}
  const [playing, setPlaying]             = useState(false);
  const [speed, setSpeed]                 = useState(600);
  const [perfVisible, setPerfVisible]     = useState(false);

  const intervalRef  = useRef(null);
  const frameAbort   = useRef(null);
  const cache        = useRef(new Map()); // event_id → frame

  const { record, renderRef, stats } = usePerf();

  // Load user list on mount
  useEffect(() => {
    const ac = new AbortController();
    fetchUserIds(ac.signal)
      .then(ids => { setUserIds(ids); if (ids.length) setUserId(ids[0]); })
      .catch(() => {});
    return () => ac.abort();
  }, []);

  // Load timeline + all viz states when user changes — clear cache
  useEffect(() => {
    if (userId == null) return;
    cache.current.clear();
    setTimeline([]);
    setVizData({});
    setSliderIdx(0);
    setAppliedIdx(0);
    setFrame(null);
    setPlaying(false);
    const ac = new AbortController();
    Promise.all([
      fetchTimeline(userId, ac.signal),
      fetchAllVizStates(userId, ac.signal),
    ])
      .then(([rows, viz]) => {
        setTimeline(rows);
        setVizData(viz);
        setSliderIdx(0);
        setAppliedIdx(0);
      })
      .catch(() => {});
    return () => ac.abort();
  }, [userId]);

  // Fetch frame for appliedIdx, with cache
  const loadFrame = useCallback(async (idx, tl, uid) => {
    const row = tl[idx];
    if (!row || uid == null) return;
    if (frameAbort.current) frameAbort.current.abort();
    frameAbort.current = new AbortController();
    const sig = frameAbort.current.signal;

    const cached = cache.current.has(row.event_id);
    const result = await record(
      () => cached
        ? Promise.resolve(cache.current.get(row.event_id))
        : fetchFrame(uid, row.event_id, sig),
      { cached }
    );
    if (!cached) cache.current.set(row.event_id, result);
    setFrame(result);
  }, [record]);

  useEffect(() => {
    loadFrame(appliedIdx, timeline, userId).catch(() => {});
  }, [appliedIdx, timeline, userId, loadFrame]);

  // Prefetch next N frames whenever appliedIdx advances
  useEffect(() => {
    for (let i = appliedIdx + 1; i <= appliedIdx + PREFETCH_AHEAD; i++) {
      const row = timeline[i];
      if (!row || cache.current.has(row.event_id)) continue;
      fetchFrame(userId, row.event_id)
        .then(f => cache.current.set(row.event_id, f))
        .catch(() => {});
    }
  }, [appliedIdx, timeline, userId]);

  // Playback
  useEffect(() => {
    if (!playing) { clearInterval(intervalRef.current); return; }
    intervalRef.current = setInterval(() => {
      setSliderIdx(prev => {
        if (prev >= timeline.length - 1) { setPlaying(false); return prev; }
        const next = prev + 1;
        setAppliedIdx(next);
        return next;
      });
    }, speed);
    return () => clearInterval(intervalRef.current);
  }, [playing, speed, timeline.length]);

  const togglePlay = () => setPlaying(p => !p);

  const eventData = timeline[sliderIdx] ?? null;

  // Accumulate viz points from checkpoint snapshot + deltas up to appliedIdx
  const points = useMemo(() => {
    if (!timeline.length || !Object.keys(vizData).length) return [];
    const currentEventId = timeline[appliedIdx]?.event_id;
    if (currentEventId == null) return [];
    const currentEntry = vizData[currentEventId];
    if (!currentEntry) return [];

    const { checkpoint_id, points_data } = currentEntry;
    if (currentEventId === checkpoint_id) return points_data;

    // Start from checkpoint snapshot and accumulate deltas forward
    const base = vizData[checkpoint_id]?.points_data ?? [];
    const acc = [...base];
    const cpIdx = timeline.findIndex(r => r.event_id === checkpoint_id);
    if (cpIdx === -1) return acc;
    for (let i = cpIdx + 1; i <= appliedIdx; i++) {
      const delta = vizData[timeline[i]?.event_id]?.points_data;
      if (delta) acc.push(...delta);
    }
    return acc;
  }, [appliedIdx, timeline, vizData]);

  const clusters = useMemo(() => frame?.clusters ?? [], [frame]);
  const recs     = useMemo(() => (frame?.recommendations ?? []).slice(0, 8), [frame]);

  // Attach local sequential index (1-based) for chart X-axis
  const chartTimeline = useMemo(
    () => timeline.map((row, i) => ({ ...row, local_index: i + 1 })),
    [timeline]
  );
  const selectedLocalIdx = sliderIdx + 1; // 1-based

  return (
    <div className="dashboard">
      <header className="dash-header">
        <div className="dash-header__top">
          <h1>Replay Dashboard</h1>
          <PerfOverlay stats={stats} visible={perfVisible} onToggle={() => setPerfVisible(v => !v)} />
        </div>

        <div className="user-selector">
          <label>User</label>
          <select
            value={userId ?? ''}
            onChange={e => { setUserId(Number(e.target.value)); setPlaying(false); }}
            disabled={!userIds.length}
          >
            {userIds.map(id => <option key={id} value={id}>User {id}</option>)}
          </select>
        </div>

        <div className="event-selector">
          <button className={`play-btn${playing ? ' play-btn--pause' : ''}`} onClick={togglePlay}
            disabled={!timeline.length} title={playing ? 'Pause' : 'Play'}>
            {playing
              ? <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><rect x="3" y="2" width="4" height="12" rx="1"/><rect x="9" y="2" width="4" height="12" rx="1"/></svg>
              : <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M4 2.5l10 5.5-10 5.5V2.5z"/></svg>
            }
          </button>
          <select className="speed-select" value={speed} onChange={e => setSpeed(Number(e.target.value))}>
            <option value={1200}>0.5×</option>
            <option value={600}>1×</option>
            <option value={300}>2×</option>
            <option value={150}>4×</option>
          </select>
          <label>Event</label>
          <input
            type="range" min={0} max={Math.max(0, timeline.length - 1)} value={sliderIdx}
            onChange={e => { setPlaying(false); setSliderIdx(Number(e.target.value)); }}
            onMouseUp={e => setAppliedIdx(Number(e.currentTarget.value))}
            onKeyUp={e => setAppliedIdx(Number(e.currentTarget.value))}
            disabled={!timeline.length}
          />
          <span className="event-label">
            {timeline.length ? `${selectedLocalIdx} / ${timeline.length}` : '—'}
          </span>
          {eventData && (
            <span className="event-meta">
              {new Date(eventData.timestamp).toLocaleDateString('ko-KR')}
              {' · '}K={eventData.k_count}
              {' · '}movie {eventData.movie_id}
            </span>
          )}
        </div>
      </header>

      <div className="grid-full">
        <MemoKTimeline
          timeline={chartTimeline}
          selectedLocalIdx={selectedLocalIdx}
          onSelect={localIdx => {
            setPlaying(false);
            const i = localIdx - 1;
            setSliderIdx(i);
            setAppliedIdx(i);
          }}
        />
      </div>

      <div className="grid-main" ref={renderRef}>
        <MemoClusterView points={points} clusterInfo={clusters} eventData={eventData} />
        <MemoRecommendations recs={recs} />
      </div>
    </div>
  );
}
