-- reach/sql/08_walk_suggest.sql
-- 駅から歩いて行ける場所を、道のりで出す（Issue #23）。
-- #6 の reach.suggest_reach_first は、駅から場所までを直線（800m〜2km）で測っていた。
-- ここでは、道路のグラフで歩く時間（分）を出し、同じ考え方で候補にする：
--   滞在時間の中で、片道 p_base_walk_min（既定10分）までは歩ける。
--   それより遠い場所は、余分に歩く往復の時間が余裕に収まれば候補（上限 p_max_walk_min、既定25分）。
-- 歩く時間 ＝ 駅の代表点から最寄りの道路の点まで（直線）＋ 道路のグラフ上の道のり ＋ 道路の点から場所まで（直線）。分速80m。

-- 1つの駅グループから、片道 p_max_min 分までに歩いて行ける場所
CREATE OR REPLACE FUNCTION reach.walk_from_station(p_group_code text, p_max_min double precision)
RETURNS TABLE (spot_id int, walk_min double precision)
LANGUAGE sql STABLE AS $$
  WITH st AS (
    SELECT sw.node_id, sw.access_m, w.geom
    FROM reach.station_walk sw JOIN reach.walk_node w USING (node_id)
    WHERE sw.group_code = p_group_code
  ),
  dd AS (
    SELECT d.node, d.agg_cost
    FROM st,
    LATERAL pgr_drivingDistance(
      format(
        -- 探す範囲の辺だけを渡す（緯度1度≒111km。余裕を見て 1度＝80km で広げる）
        'SELECT id, source, target, cost, reverse_cost FROM reach.walk_edge WHERE geom && ST_Expand(%L::geometry, %s)',
        st.geom, (p_max_min * 80.0) / 80000.0),
      st.node_id, GREATEST(p_max_min - st.access_m / 80.0, 0), directed := false) d
  )
  SELECT sw.spot_id,
         min(dd.agg_cost + (SELECT access_m FROM st) / 80.0 + sw.access_m / 80.0)
  FROM dd
  JOIN reach.spot_walk sw ON sw.node_id = dd.node
  GROUP BY sw.spot_id
  HAVING min(dd.agg_cost + (SELECT access_m FROM st) / 80.0 + sw.access_m / 80.0) <= p_max_min;
$$;

-- 駅と場所の組の歩く時間を、前もって計算する（片道25分まで）。
-- 駅グループごとに道路のグラフをたどるので、時間がかかる（5339 全体で数分〜十数分の見込み）。
TRUNCATE reach.station_spot_walk;
INSERT INTO reach.station_spot_walk (group_code, spot_id, walk_min)
SELECT sw.group_code, f.spot_id, f.walk_min
FROM reach.station_walk sw
CROSS JOIN LATERAL reach.walk_from_station(sw.group_code, 25) f;
ANALYZE reach.station_spot_walk;

-- 寄り道の提案（案1）の、道のり版。前もって計算した reach.station_spot_walk を使う。
-- 返す列は reach.suggest_reach_first に walk_min を足したもの
CREATE OR REPLACE FUNCTION reach.suggest_walk(
  p_from          text,
  p_to            text,
  p_available_min double precision,
  p_stay_min      double precision,
  p_qvec          vector,
  p_base_walk_min double precision DEFAULT 10,
  p_max_walk_min  double precision DEFAULT 25
)
RETURNS TABLE (
  spot_id int, source text, name text, kinds text,
  group_code text, station_name text, dist_m numeric,
  t1_min numeric, t2_min numeric, slack_min numeric, detour_min numeric, sim double precision,
  walk_min numeric
)
LANGUAGE sql STABLE AS $$
  WITH r AS (
    SELECT * FROM reach.reachable(p_from, p_to, p_available_min, p_stay_min)
  ),
  direct AS (SELECT min(r.t1_min + r.t2_min) AS t FROM r),
  ok AS (
    SELECT r.group_code, r.station_name, r.t1_min, r.t2_min, r.geom AS st_geom, w.spot_id, w.walk_min,
           r.slack_min - 2 * GREATEST(0, w.walk_min - p_base_walk_min) AS slack_left
    FROM r
    JOIN reach.station_spot_walk w USING (group_code)
    WHERE w.walk_min <= LEAST(p_max_walk_min, p_base_walk_min + r.slack_min::double precision / 2)
  ),
  best AS (
    SELECT DISTINCT ON (ok.spot_id) ok.*
    FROM ok
    ORDER BY ok.spot_id, ok.t1_min + ok.t2_min, ok.walk_min
  )
  SELECT s.spot_id, s.source, s.name, s.kinds,
         b.group_code, b.station_name,
         round(ST_Distance(s.geom::geography, b.st_geom::geography)::numeric, 0),
         b.t1_min, b.t2_min, round(b.slack_left::numeric, 0),
         (b.t1_min + b.t2_min) - (SELECT t FROM direct),
         1 - (s.embedding <=> p_qvec),
         round(b.walk_min::numeric, 1)
  FROM best b
  JOIN reach.spot s USING (spot_id);
$$;
