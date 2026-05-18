import { useState, useEffect, useRef } from 'react';
import { fetchUserIds, fetchTimeline, fetchFrame } from './api';
import KTimeline from './components/KTimeline';
import ClusterView from './components/ClusterView';
import Recommendations from './components/Recommendations';
import './App.css';

export default function App() {
  const [userIds, setUserIds]         = useState([]);
  const [userId, setUserId]           = useState(null);
  const [timeline, setTimeline]       = useState([]);
  const [sliderIdx, setSliderIdx]     = useState(0); // slider UI position (no API call)
  const [appliedIdx, setAppliedIdx]   = useState(0); // committed index → triggers API fetch
  const [frame, setFrame]             = useState(null);
  const [playing, setPlaying]         = useState(false);
  const [speed, setSpeed]             = useState(600);
  const intervalRef = useRef(null);
  const frameAbort  = useRef(null);

  // Load user list on mount
  useEffect(() => {
    const ac = new AbortController();
    fetchUserIds(ac.signal)
      .then(ids => { setUserIds(ids); if (ids.length) setUserId(ids[0]); })
      .catch(() => {});
    return () => ac.abort();
  }, []);

  // Load timeline when user changes
  useEffect(() => {
    if (userId == null) return;
    setTimeline([]);
    setSliderIdx(0);
    setAppliedIdx(0);
    setFrame(null);
    setPlaying(false);
    const ac = new AbortController();
    fetchTimeline(userId, ac.signal)
      .then(rows => { setTimeline(rows); setSliderIdx(0); setAppliedIdx(0); })
      .catch(() => {});
    return () => ac.abort();
  }, [userId]);

  // Load frame only when appliedIdx is committed
  useEffect(() => {
    const row = timeline[appliedIdx];
    if (!row || userId == null) return;
    if (frameAbort.current) frameAbort.current.abort();
    frameAbort.current = new AbortController();
    fetchFrame(userId, row.event_id, frameAbort.current.signal)
      .then(setFrame)
      .catch(() => {});
  }, [userId, timeline, appliedIdx]);

  // Playback — advances both slider and applied together
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
  const points    = frame?.visualization?.points_data ?? [];
  const clusters  = frame?.clusters ?? [];
  const recs      = frame?.recommendations ?? [];

  return (
    <div className="dashboard">
      <header className="dash-header">
        <h1>Replay Dashboard</h1>

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
            {eventData ? `#${eventData.event_id}` : '—'}
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
        <KTimeline
          timeline={timeline}
          selectedIdx={eventData?.event_id}
          onSelect={id => { setPlaying(false); const i = timeline.findIndex(r => r.event_id === id); setSliderIdx(i); setAppliedIdx(i); }}
        />
      </div>

      <div className="grid-main">
        <ClusterView points={points} clusterInfo={clusters} eventData={eventData} />
        <Recommendations recs={recs.slice(0, 6)} />
      </div>
    </div>
  );
}
