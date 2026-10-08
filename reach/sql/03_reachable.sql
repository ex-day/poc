-- reach/sql/03_reachable.sql
-- 寄り道できる駅のリストを返す関数（Issue #5）。
--
--   SELECT * FROM reach.reachable('新大阪', '新横浜', 360, 90);
--
-- 出発駅から行ける時間 t1 と、その駅から帰着駅へ戻る時間 t2 を、
-- 時刻表を使わずに「距離 ÷ 表定速度 ＋ 乗換の時間」で粗く出す。
-- t1 ＋ 滞在時間 ＋ t2 ≦ 使える時間 を満たす駅（駅グループ単位）を、余裕（slack）つきで返す。
-- 駅の指定は、駅名またはグループコード。同じ駅名のグループが複数あるときは、グループコードで指定する。
--
-- 1つの駅グループは、ホームの両端の点を複数持つ（新大阪で10点）。pgr_drivingDistance に開始点を複数渡すと、
-- 開始点ごとに探索を1回ずつ行うため遅い（新大阪で約4秒）。そこで、仮の点 0 を置き、そこから各開始点へ
-- 所要時間 0 の辺を足して、仮の点から1回だけ探索する。ほしいのは開始点のうちの最小の時間なので、結果は同じ。
-- （equicost := true も試したが、3駅で1分ずれた。仮の点のほうが、複数の開始点の最小と正確に一致する）

CREATE OR REPLACE FUNCTION reach.group_nodes(p_station text)
RETURNS bigint[] LANGUAGE plpgsql STABLE AS $$
DECLARE
  groups text[];
  nodes bigint[];
BEGIN
  SELECT array_agg(DISTINCT group_code) INTO groups
  FROM reach.station WHERE station_name = p_station OR group_code = p_station;
  IF groups IS NULL THEN
    RAISE EXCEPTION '駅が見つからない：%', p_station;
  ELSIF cardinality(groups) > 1 THEN
    RAISE EXCEPTION '同じ名前の駅が複数ある：%（グループコード %）。グループコードで指定してください', p_station, groups;
  END IF;
  SELECT array_agg(DISTINCT n) INTO nodes
  FROM reach.station s, unnest(ARRAY[s.node_a, s.node_b]) n
  WHERE s.group_code = groups[1] AND n IS NOT NULL;
  RETURN nodes;
END $$;

-- pgRouting に渡す辺の SQL。仮の点 0 から、開始点のそれぞれへ所要時間 0 の辺を足す。
CREATE OR REPLACE FUNCTION reach.edges_from(p_nodes bigint[])
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT format(
    'SELECT id, source, target, cost, reverse_cost FROM reach.edge
     UNION ALL
     SELECT -n, 0, n, 0, 0 FROM unnest(%L::bigint[]) AS n',
    p_nodes)
$$;

CREATE OR REPLACE FUNCTION reach.reachable(
  p_from          text,                          -- 出発駅
  p_to            text,                          -- 帰着駅
  p_available_min double precision,              -- 使える時間（分）：出発から帰着の期限まで
  p_stay_min      double precision,              -- 寄り道先での滞在時間（分）
  p_wait_min      double precision DEFAULT 5     -- 乗り始めの待ち時間（分）。行き・帰りのそれぞれに足す
)
RETURNS TABLE (
  group_code   text,
  station_name text,
  lines        text,
  t1_min       numeric,  -- 出発駅 → この駅
  t2_min       numeric,  -- この駅 → 帰着駅
  slack_min    numeric,  -- 余裕
  geom         geometry
)
LANGUAGE sql STABLE AS $$
  WITH
  budget AS (
    SELECT p_available_min - p_stay_min - 2 * p_wait_min AS move_max
  ),
  from_dd AS (
    SELECT node, min(agg_cost) AS t
    FROM pgr_drivingDistance(
      reach.edges_from(reach.group_nodes(p_from)), 0, (SELECT move_max FROM budget), directed := true)
    GROUP BY node
  ),
  to_dd AS (
    SELECT node, min(agg_cost) AS t
    FROM pgr_drivingDistance(
      reach.edges_from(reach.group_nodes(p_to)), 0, (SELECT move_max FROM budget), directed := true)
    GROUP BY node
  ),
  per_station AS (
    SELECT s.group_code, s.station_name, l.operator || ' ' || l.line_name AS line,
           LEAST(fa.t, fb.t) + p_wait_min AS t1,
           LEAST(ta.t, tb.t) + p_wait_min AS t2,
           s.geom
    FROM reach.station s
    JOIN reach.line l USING (line_id)
    LEFT JOIN from_dd fa ON fa.node = s.node_a
    LEFT JOIN from_dd fb ON fb.node = s.node_b
    LEFT JOIN to_dd   ta ON ta.node = s.node_a
    LEFT JOIN to_dd   tb ON tb.node = s.node_b
  ),
  per_group AS (
    SELECT group_code,
           min(station_name) AS station_name,
           string_agg(DISTINCT line, '、') AS lines,
           min(t1) AS t1, min(t2) AS t2,
           ST_Centroid(ST_Collect(geom)) AS geom
    FROM per_station
    WHERE t1 IS NOT NULL AND t2 IS NOT NULL
    GROUP BY group_code
  )
  SELECT g.group_code, g.station_name, g.lines,
         round(g.t1::numeric, 0), round(g.t2::numeric, 0),
         round((p_available_min - (g.t1 + p_stay_min + g.t2))::numeric, 0),
         g.geom
  FROM per_group g
  WHERE g.t1 + p_stay_min + g.t2 <= p_available_min
    AND g.group_code NOT IN (
      SELECT s.group_code FROM reach.station s
      WHERE s.station_name IN (p_from, p_to) OR s.group_code IN (p_from, p_to))
  ORDER BY 6 DESC;
$$;
