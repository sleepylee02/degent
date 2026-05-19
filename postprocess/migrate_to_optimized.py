import sqlite3
import json
from pathlib import Path
from tqdm import tqdm
import pandas as pd

# 1. 파일 경로 설정
SOURCE_DB = Path("outputs/post/temporal_2022_events_3000_new_versions_history/dashboard_compact/dashboard_compact.sqlite")
TARGET_DB = Path("outputs/dashboard_optimized.sqlite")

def run_etl():
    if not SOURCE_DB.exists():
        print(f"❌ 원본 DB를 찾을 수 없습니다: {SOURCE_DB}")
        return

    # 타겟 DB가 이미 존재하면 삭제 (초기화)
    if TARGET_DB.exists():
        TARGET_DB.unlink()

    conn_src = sqlite3.connect(SOURCE_DB)
    conn_tgt = sqlite3.connect(TARGET_DB)
    
    # 2. 타겟 DB 스키마 생성 (checkpoint_id 포함)
    conn_tgt.executescript("""
        CREATE TABLE event_timeline (
            user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            movie_id INTEGER NOT NULL,
            is_refit_triggered INTEGER NOT NULL DEFAULT 0,
            refit_reason TEXT,
            k_count INTEGER NOT NULL DEFAULT 0,
            noise_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, event_id)
        );

        CREATE TABLE visualization_states (
            user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            checkpoint_id INTEGER NOT NULL, 
            points_data TEXT NOT NULL,
            PRIMARY KEY (user_id, event_id)
        );

        CREATE TABLE cluster_snapshots (
            user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            cluster_id INTEGER NOT NULL,
            size INTEGER NOT NULL,
            top_genres TEXT,
            PRIMARY KEY (user_id, event_id, cluster_id)
        );

        CREATE TABLE recommendations (
            user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            movie_id INTEGER NOT NULL,
            score REAL,
            src_cluster INTEGER,
            PRIMARY KEY (user_id, event_id, rank)
        );
    """)

    # 3. refit이 최소 3번 이상 있는 유저만 추출
    print("🔍 조건에 맞는 유저(Refit 2회 이상)를 탐색 중...")
    valid_users_query = """
        SELECT user_id 
        FROM event_timeline 
        GROUP BY user_id 
        HAVING SUM(is_refit_triggered) >= 2
    """
    valid_users = pd.read_sql_query(valid_users_query, conn_src)['user_id'].tolist()
    print(f"총 {len(valid_users)}명의 유저가 마이그레이션 대상입니다.\n")

    # 4. 유저별 데이터 변환 및 적재
    cursor_src = conn_src.cursor()
    cursor_tgt = conn_tgt.cursor()

    for user_id in tqdm(valid_users, desc="ETL 진행률"):
        
        # [A] event_timeline 복사
        cursor_src.execute("""
            SELECT user_id, event_id, timestamp, movie_id,
                   is_refit_triggered, refit_reason, k_count, noise_count
            FROM event_timeline WHERE user_id = ?
        """, (user_id,))
        timeline_data = cursor_src.fetchall()
        cursor_tgt.executemany(
            "INSERT INTO event_timeline VALUES (?, ?, ?, ?, ?, ?, ?, ?)", timeline_data
        )

        # [B] cluster_snapshots 복사
        cursor_src.execute("""
            SELECT user_id, event_id, cluster_id, size, top_genres
            FROM cluster_snapshots WHERE user_id = ?
        """, (user_id,))
        cluster_data = cursor_src.fetchall()
        cursor_tgt.executemany(
            "INSERT INTO cluster_snapshots VALUES (?, ?, ?, ?, ?)", cluster_data
        )

        # [C] recommendations 복사 (rank <= 10)
        cursor_src.execute("""
            SELECT user_id, event_id, rank, movie_id, score, src_cluster
            FROM recommendations WHERE user_id = ? AND rank <= 10
        """, (user_id,))
        reco_data = cursor_src.fetchall()
        cursor_tgt.executemany(
            "INSERT INTO recommendations VALUES (?, ?, ?, ?, ?, ?)", reco_data
        )

        # [D] visualization_states 변환 (Checkpoint & Delta 계산)
        # 타임라인 테이블과 조인하여 is_refit_triggered 값을 함께 가져옴 (시간순 정렬 필수)
        cursor_src.execute("""
            SELECT v.event_id, v.points_data, e.is_refit_triggered 
            FROM visualization_states v
            JOIN event_timeline e ON v.user_id = e.user_id AND v.event_id = e.event_id
            WHERE v.user_id = ?
            ORDER BY v.event_id ASC
        """, (user_id,))
        
        viz_rows = cursor_src.fetchall()
        
        last_checkpoint_id = None
        prev_points_length = 0
        new_viz_data = []

        for i, (event_id, points_json, is_refit) in enumerate(viz_rows):
            all_points = json.loads(points_json)
            
            # 최초 프레임이거나, Refit이 발생한 경우 -> Checkpoint 생성 (전체 저장)
            if i == 0 or is_refit == 1:
                last_checkpoint_id = event_id
                points_to_save = all_points
            # 그 외의 경우 -> Delta(추가된 점)만 저장
            else:
                # 스트리밍 환경이므로 이전 프레임 대비 뒤에 새로 추가된 점들만 잘라냄(Slicing)
                points_to_save = all_points[prev_points_length:]
            
            new_viz_data.append((
                user_id, 
                event_id, 
                last_checkpoint_id, 
                json.dumps(points_to_save)
            ))
            
            # 다음 프레임의 Delta 계산을 위해 현재 점의 개수를 기억
            prev_points_length = len(all_points)

        cursor_tgt.executemany(
            "INSERT INTO visualization_states VALUES (?, ?, ?, ?)", new_viz_data
        )

    # 5. 커밋 및 인덱스 생성
    conn_tgt.commit()
    
    # 조회 성능 향상을 위한 인덱스
    cursor_tgt.executescript("""
        CREATE INDEX idx_timeline_user ON event_timeline(user_id);
        CREATE INDEX idx_viz_user ON visualization_states(user_id);
        CREATE INDEX idx_viz_checkpoint ON visualization_states(user_id, checkpoint_id);
    """)
    
    conn_src.close()
    conn_tgt.close()

    old_size = SOURCE_DB.stat().st_size / (1024 * 1024)
    new_size = TARGET_DB.stat().st_size / (1024 * 1024)
    print(f"\n마이그레이션 완료! ({old_size:.2f} MB  ->  {new_size:.2f} MB)")

if __name__ == "__main__":
    run_etl()