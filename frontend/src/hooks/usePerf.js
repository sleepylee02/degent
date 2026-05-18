import { useRef, useState, useCallback } from 'react';

/**
 * Measures fetch latency and render time.
 * Returns { record, renderRef, stats }
 *
 * Usage:
 *   const { record, renderRef, stats } = usePerf();
 *   const frame = await record('fetch', () => fetchFrame(...));
 *   <div ref={renderRef}>...</div>   // measures time-to-paint after data arrives
 */
export function usePerf() {
  const [stats, setStats] = useState({
    fetchMs: null,
    renderMs: null,
    cacheHits: 0,
    cacheMisses: 0,
    history: [], // last 20 samples { fetchMs, renderMs, cached }
  });

  const fetchStartRef = useRef(null);
  const renderRef     = useRef(null);
  const observerRef   = useRef(null);

  // Call this wrapping your fetch. Returns the fetch result.
  const record = useCallback(async (fetcher, { cached = false } = {}) => {
    // Tear down previous observer
    if (observerRef.current) { observerRef.current.disconnect(); observerRef.current = null; }

    const t0 = performance.now();
    const result = await fetcher();
    const fetchMs = +(performance.now() - t0).toFixed(1);
    fetchStartRef.current = performance.now();

    // Measure time until the DOM node actually paints
    if (renderRef.current) {
      observerRef.current = new MutationObserver(() => {
        const renderMs = +(performance.now() - fetchStartRef.current).toFixed(1);
        observerRef.current.disconnect();
        observerRef.current = null;
        setStats(prev => {
          const sample = { fetchMs, renderMs, cached };
          const history = [...prev.history.slice(-19), sample];
          return {
            fetchMs,
            renderMs,
            cacheHits:   prev.cacheHits   + (cached ? 1 : 0),
            cacheMisses: prev.cacheMisses + (cached ? 0 : 1),
            history,
          };
        });
      });
      observerRef.current.observe(renderRef.current, { childList: true, subtree: true, characterData: true, attributes: true });
    }

    return result;
  }, []);

  return { record, renderRef, stats };
}
