-- reach/sql/05_suggest.sql
-- 寄り道できる駅のリストと「場所」（reach.spot）を組み合わせて、寄り道の提案の候補を出す（Issue #6）。
-- 並べ方は呼び出す側（suggest.py）で変えて比べるので、ここでは候補と材料を返す。
--
-- 駅のまわりの範囲（仮）：
--   滞在時間の中で、駅から base_radius_m（既定 800m ≒ 片道10分）までは歩けるものとする。
--   それより遠い場所は、余分に歩く往復の時間が余裕（slack）に収まれば候補にする。
--   つまり、半径 ＝ base_radius_m ＋ 歩く速さ × 余裕 ÷ 2（上限 max_radius_m）。距離は直線。
--
-- 遠回り（detour_min）：その駅を経由した t1＋t2 から、出発地から帰着地へまっすぐ行く時間を引いたもの。
--   まっすぐ行く時間は、寄り道できる駅のうち t1＋t2 が最小のもの（まっすぐの経路の途中の駅）で近似する。

-- 案1：行ける駅 → 駅のまわりの場所。興味との近さ（sim）は、比べるために付けるだけで、絞り込みには使わない。
CREATE OR REPLACE FUNCTION reach.suggest_reach_first(
  p_from          text,
  p_to            text,
  p_available_min double precision,
  p_stay_min      double precision,
  p_qvec          vector,
  p_base_radius_m double precision DEFAULT 800,
  p_max_radius_m  double precision DEFAULT 2000,
  p_walk_m_per_min double precision DEFAULT 80
)
RETURNS TABLE (
  spot_id int, source text, name text, kinds text,
  group_code text, station_name text, dist_m numeric,
  t1_min numeric, t2_min numeric, slack_min numeric, detour_min numeric, sim double precision
)
LANGUAGE sql STABLE AS $$
  WITH r AS (
    SELECT * FROM reach.reachable(p_from, p_to, p_available_min, p_stay_min)
  ),
  direct AS (SELECT min(r.t1_min + r.t2_min) AS t FROM r),
  near AS (
    SELECT s.spot_id, r.group_code, r.station_name, r.t1_min, r.t2_min, r.slack_min,
           ST_Distance(s.geom::geography, r.geom::geography) AS d
    FROM r
    JOIN reach.spot s ON ST_DWithin(s.geom::geography, r.geom::geography, p_max_radius_m)
  ),
  ok AS (
    SELECT n.*,
           -- 余分に歩く往復の時間を、余裕から引く
           n.slack_min - 2 * GREATEST(0, n.d - p_base_radius_m) / p_walk_m_per_min AS slack_left
    FROM near n
    WHERE n.d <= LEAST(p_max_radius_m, p_base_radius_m + p_walk_m_per_min * n.slack_min / 2)
  ),
  best AS (  -- 同じ場所に複数の駅から行けるときは、遠回りが小さい駅を使う
    SELECT DISTINCT ON (ok.spot_id) ok.*
    FROM ok
    ORDER BY ok.spot_id, ok.t1_min + ok.t2_min, ok.d
  )
  SELECT s.spot_id, s.source, s.name, s.kinds,
         b.group_code, b.station_name, round(b.d::numeric, 0),
         b.t1_min, b.t2_min, round(b.slack_left::numeric, 0),
         (b.t1_min + b.t2_min) - (SELECT t FROM direct),
         1 - (s.embedding <=> p_qvec)
  FROM best b
  JOIN reach.spot s USING (spot_id);
$$;

-- 案2：興味で場所を上位 k 件まで探す → そのうち、行ける駅のまわりにあるものだけ残す。
CREATE OR REPLACE FUNCTION reach.suggest_interest_first(
  p_from          text,
  p_to            text,
  p_available_min double precision,
  p_stay_min      double precision,
  p_qvec          vector,
  p_k             int,
  p_base_radius_m double precision DEFAULT 800,
  p_max_radius_m  double precision DEFAULT 2000,
  p_walk_m_per_min double precision DEFAULT 80
)
RETURNS TABLE (
  spot_id int, source text, name text, kinds text,
  group_code text, station_name text, dist_m numeric,
  t1_min numeric, t2_min numeric, slack_min numeric, detour_min numeric, sim double precision
)
LANGUAGE sql STABLE AS $$
  WITH top AS (
    SELECT s.spot_id, 1 - (s.embedding <=> p_qvec) AS sim
    FROM reach.spot s
    ORDER BY s.embedding <=> p_qvec
    LIMIT p_k
  ),
  r AS (
    SELECT * FROM reach.reachable(p_from, p_to, p_available_min, p_stay_min)
  ),
  direct AS (SELECT min(r.t1_min + r.t2_min) AS t FROM r),
  ok AS (
    SELECT t.spot_id, t.sim, r.group_code, r.station_name, r.t1_min, r.t2_min,
           ST_Distance(s.geom::geography, r.geom::geography) AS d,
           r.slack_min - 2 * GREATEST(0, ST_Distance(s.geom::geography, r.geom::geography) - p_base_radius_m) / p_walk_m_per_min AS slack_left
    FROM top t
    JOIN reach.spot s USING (spot_id)
    JOIN r ON ST_DWithin(s.geom::geography, r.geom::geography, p_max_radius_m)
    WHERE ST_Distance(s.geom::geography, r.geom::geography)
          <= LEAST(p_max_radius_m, p_base_radius_m + p_walk_m_per_min * r.slack_min / 2)
  ),
  best AS (
    SELECT DISTINCT ON (ok.spot_id) ok.*
    FROM ok
    ORDER BY ok.spot_id, ok.t1_min + ok.t2_min, ok.d
  )
  SELECT s.spot_id, s.source, s.name, s.kinds,
         b.group_code, b.station_name, round(b.d::numeric, 0),
         b.t1_min, b.t2_min, round(b.slack_left::numeric, 0),
         (b.t1_min + b.t2_min) - (SELECT t FROM direct),
         b.sim
  FROM best b
  JOIN reach.spot s USING (spot_id);
$$;
