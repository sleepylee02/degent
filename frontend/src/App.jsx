import { useState, useEffect, useRef, useCallback, useMemo, memo } from 'react';
import { fetchUserIds, fetchTimeline, fetchFrame, fetchVizChunk } from './api';
import KTimeline from './components/KTimeline';
import ClusterView from './components/ClusterView';
import Recommendations from './components/Recommendations';
import PerfOverlay from './components/PerfOverlay';
import { usePerf } from './hooks/usePerf';
import './App.css';

const PREFETCH_AHEAD = 3;  // frames to prefetch during playback
const CACHE_MAX      = 150; // max frame cache entries before LRU eviction

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
  const [playing, setPlaying]             = useState(false);
  const [speed, setSpeed]                 = useState(600);
  const [perfVisible, setPerfVisible]     = useState(false);
  const [chunkVersion, setChunkVersion]   = useState(0); // increments when a chunk loads → triggers points recompute

  const intervalRef   = useRef(null);
  const frameAbort    = useRef(null);
  const cache         = useRef(new Map()); // event_id → frame  (LRU-evicted at CACHE_MAX)
  const vizChunkCache = useRef(new Map()); // checkpoint_id → chunk
  const pointsAccRef  = useRef({ cpId: null, idx: -1, pts: [], version: -1 }); // incremental acc cache

  const { record, renderRef, stats } = usePerf();

  // Load user list on mount
  useEffect(() => {
    const ac = new AbortController();
    fetchUserIds(ac.signal)
      .then(ids => { setUserIds(ids); if (ids.length) setUserId(ids[0]); })
      .catch(() => {});
    return () => ac.abort();
  }, []);

  // Load timeline when user changes — clear all caches
  useEffect(() => {
    if (userId == null) return;
    cache.current.clear();
    vizChunkCache.current.clear();
    setTimeline([]);
    setSliderIdx(0);
    setAppliedIdx(0);
    setFrame(null);
    setChunkVersion(0);
    setPlaying(false);
    const ac = new AbortController();
    fetchTimeline(userId, ac.signal)
      .then(rows => { setTimeline(rows); setSliderIdx(0); setAppliedIdx(0); })
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
    if (!cached) {
      cache.current.set(row.event_id, result);
      // LRU eviction: Map preserves insertion order — delete oldest entry
      if (cache.current.size > CACHE_MAX) {
        cache.current.delete(cache.current.keys().next().value);
      }
    }
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

  // Derive current checkpoint_id from timeline (first event + refit events)
  const currentCheckpointId = useMemo(() => {
    if (!timeline.length) return null;
    let cpId = timeline[0].event_id;
    for (let i = 1; i <= appliedIdx; i++) {
      if (timeline[i]?.is_refit_triggered) cpId = timeline[i].event_id;
    }
    return cpId;
  }, [appliedIdx, timeline]);

  // Next checkpoint_id — for prefetch
  const nextCheckpointId = useMemo(() => {
    for (let i = appliedIdx + 1; i < timeline.length; i++) {
      if (timeline[i].is_refit_triggered) return timeline[i].event_id;
    }
    return null;
  }, [appliedIdx, timeline]);

  // Load current chunk; prefetch next chunk
  useEffect(() => {
    if (userId == null || currentCheckpointId == null) return;
    const load = (cpId) => {
      if (vizChunkCache.current.has(cpId)) return;
      fetchVizChunk(userId, cpId)
        .then(chunk => {
          vizChunkCache.current.set(cpId, chunk);
          setChunkVersion(v => v + 1);
        })
        .catch(() => {});
    };
    load(currentCheckpointId);
    if (nextCheckpointId != null) load(nextCheckpointId);
  }, [userId, currentCheckpointId, nextCheckpointId]);

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

  // Incrementally accumulate viz points — forward playback appends only new deltas
  const points = useMemo(() => {
    if (!timeline.length || currentCheckpointId == null) return [];
    const chunk = vizChunkCache.current.get(currentCheckpointId);
    if (!chunk) return [];

    const prev = pointsAccRef.current;
    const cpIdx = timeline.findIndex(r => r.event_id === currentCheckpointId);

    let pts;
    if (
      prev.cpId === currentCheckpointId &&
      prev.version === chunkVersion &&
      appliedIdx >= prev.idx           // forward only — backward falls through to full rebuild
    ) {
      // Extend: copy prev array once, append only new deltas
      pts = prev.idx === appliedIdx ? prev.pts : [...prev.pts];
      for (let i = prev.idx + 1; i <= appliedIdx; i++) {
        const delta = chunk[timeline[i]?.event_id]?.points_data;
        if (delta) pts.push(...delta);
      }
    } else {
      // Full rebuild: checkpoint changed, chunk reloaded, or backward seek
      pts = [...(chunk[currentCheckpointId]?.points_data ?? [])];
      for (let i = cpIdx + 1; i <= appliedIdx; i++) {
        const delta = chunk[timeline[i]?.event_id]?.points_data;
        if (delta) pts.push(...delta);
      }
    }

    pointsAccRef.current = { cpId: currentCheckpointId, idx: appliedIdx, pts, version: chunkVersion };
    return pts;
  }, [appliedIdx, timeline, currentCheckpointId, chunkVersion]); // eslint-disable-line

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
