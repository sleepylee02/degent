const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';
const posterUrl = (movieId) => `${API_BASE}/posters/${movieId}.jpg`;

const CLUSTER_COLORS = [
  '#3b5bdb', '#0ca678', '#e67700', '#c92a2a', '#862e9c', '#1864ab',
];

function clusterColor(label) {
  return CLUSTER_COLORS[label % CLUSTER_COLORS.length];
}

function MovieCard({ rec }) {
  const color = clusterColor(rec.src_cluster);
  return (
    <div className={`movie-card${rec.is_hit ? ' movie-card--hit' : ''}`}>
      <div className="movie-card__poster">
        <span className="movie-card__rank">#{rec.rank}</span>
        <img
          src={posterUrl(rec.movie_id)}
          alt={rec.title}
          className="movie-card__img"
          onError={e => { e.currentTarget.style.display = 'none'; e.currentTarget.nextSibling.style.display = 'block'; }}
        />
        <span className="movie-card__no-img" style={{ display: 'none' }}>No image</span>
        {rec.is_hit && <span className="movie-card__hit-badge">Hit ✓</span>}
      </div>
      <div className="movie-card__body">
        <p className="movie-card__title">{rec.title}</p>
        <div className="movie-card__genres">
          {rec.genres.map(g => (
            <span key={g} className="genre-tag">{g}</span>
          ))}
        </div>
        <div className="movie-card__footer">
          <span className="cluster-tag" style={{ borderColor: color, color }}>
            <span className="cluster-dot" style={{ background: color }} />
            Cluster {rec.src_cluster}
          </span>
          <span className="movie-card__score">score {rec.score.toFixed(4)}</span>
        </div>
      </div>
    </div>
  );
}

export default function Recommendations({ recs }) {
  return (
    <div className="panel panel--recs">
      <h2>추천 영화</h2>
      <div className="movie-grid">
        {recs.map(r => <MovieCard key={r.rank} rec={r} />)}
      </div>
    </div>
  );
}
