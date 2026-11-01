-- reach/sql/12_detour.sql
-- 帰り道の寄り道を「過ごせる時間」で見せる（Issue #42、ex-day/platform#134）。
-- 前提：01〜03（鉄道のグラフと reach.edges_from）、04 と load_spots.py（reach.spot）、11（reach.name_nodes）。
--
--   SELECT * FROM reach.detour_stations('新大阪', '横浜', 300) ORDER BY t1_min;   -- 駅ごとの t1・t2・余裕
--   SELECT * FROM reach.detour_route('新大阪', '横浜');                          -- 最短の経路の駅（経路の順）
--   SELECT reach.detour_map_json('新大阪', '横浜', 300);                         -- 地図の画面（reach/map/route.html）が読む形
--
-- 寄れる時間（余裕）＝ 使える時間 −（起点からこの駅まで t1）−（この駅から終点まで t2）。#5 の reach.reachable と同じ考え方で、
-- 滞在時間を引数に取らず、余裕そのものを「この駅で過ごせる時間」として返す（platform#134：色は過ごせる時間にそろえる）。
-- 場所で過ごせる時間 ＝ 駅の余裕 − 駅との往復の歩き（直線の距離 × 遠回り1.3 ÷ 80m/分、片道15分まで）。

-- 駅ごとの t1・t2・余裕。余裕が0以上の駅（駅グループ単位）を返す。
-- role：origin（起点）／dest（終点）／via（途中）。起点の余裕は「出発の前に起点のまわりで過ごせる時間」。
CREATE OR REPLACE FUNCTION reach.detour_stations(
  p_from          text,
  p_to            text,
  p_available_min double precision,
  p_wait_min      double precision DEFAULT 5
)
RETURNS TABLE (group_code text, station_name text, lines text,
               t1_min numeric, t2_min numeric, slack_min numeric, role text, geom geometry)
LANGUAGE plpgsql STABLE AS $$
#variable_conflict use_column
DECLARE
  from_nodes bigint[] := reach.name_nodes(p_from);
  to_nodes   bigint[] := reach.name_nodes(p_to);
  move_max   double precision := GREATEST(p_available_min - p_wait_min, 0);
BEGIN
  RETURN QUERY
  WITH
  from_g AS (SELECT DISTINCT s.group_code FROM reach.station s WHERE s.station_name = p_from OR s.group_code = p_from),
  to_g   AS (SELECT DISTINCT s.group_code FROM reach.station s WHERE s.station_name = p_to   OR s.group_code = p_to),
  from_dd AS (
    SELECT d.node, min(d.agg_cost) AS t
    FROM pgr_drivingDistance(reach.edges_from(from_nodes), 0, move_max, directed := true) d GROUP BY d.node
  ),
  -- 終点からの時間を、この駅から終点までの時間として使う（#5 と同じく、辺の上りと下りの所要時間は同じとみなす）
  to_dd AS (
    SELECT d.node, min(d.agg_cost) AS t
    FROM pgr_drivingDistance(reach.edges_from(to_nodes), 0, move_max, directed := true) d GROUP BY d.node
  ),
  per_station AS (
    SELECT s.group_code, s.station_name, l.operator || ' ' || l.line_name AS line,
           CASE WHEN s.group_code IN (SELECT f.group_code FROM from_g f) THEN 0
                ELSE LEAST(fa.t, fb.t) + p_wait_min END AS t1,
           CASE WHEN s.group_code IN (SELECT t.group_code FROM to_g t) THEN 0
                ELSE LEAST(ta.t, tb.t) + p_wait_min END AS t2,
           s.geom
    FROM reach.station s
    JOIN reach.line l USING (line_id)
    LEFT JOIN from_dd fa ON fa.node = s.node_a
    LEFT JOIN from_dd fb ON fb.node = s.node_b
    LEFT JOIN to_dd   ta ON ta.node = s.node_a
    LEFT JOIN to_dd   tb ON tb.node = s.node_b
  )
  SELECT ps.group_code, min(ps.station_name), string_agg(DISTINCT ps.line, '、'),
         round(min(ps.t1)::numeric, 0), round(min(ps.t2)::numeric, 0),
         round((p_available_min - min(ps.t1) - min(ps.t2))::numeric, 0),
         CASE WHEN ps.group_code IN (SELECT f.group_code FROM from_g f) THEN 'origin'
              WHEN ps.group_code IN (SELECT t.group_code FROM to_g t) THEN 'dest'
              ELSE 'via' END,
         ST_Centroid(ST_Collect(ps.geom))
  FROM per_station ps
  WHERE ps.t1 IS NOT NULL AND ps.t2 IS NOT NULL
  GROUP BY ps.group_code
  HAVING p_available_min - min(ps.t1) - min(ps.t2) >= 0;
END $$;

-- 最短の経路の上の駅（駅グループ単位）を、経路の順に。t1 は起点からその駅までの時間（待ちを含む）。
-- 画面の「経路の順の一覧」の背骨と、最初の画角に使う。経路の駅のすべてに列車が停まるとは限らない（停車駅は持っていない）。
CREATE OR REPLACE FUNCTION reach.detour_route(
  p_from     text,
  p_to       text,
  p_wait_min double precision DEFAULT 5
)
RETURNS TABLE (seq int, group_code text, station_name text, t1_min numeric, geom geometry)
LANGUAGE plpgsql STABLE AS $$
#variable_conflict use_column
DECLARE
  from_nodes bigint[] := reach.name_nodes(p_from);
  to_nodes   bigint[] := reach.name_nodes(p_to);
BEGIN
  RETURN QUERY
  WITH
  paths AS (
    SELECT d.end_vid, d.node, d.agg_cost
    FROM pgr_dijkstra(reach.edges_from(from_nodes), 0, to_nodes, directed := true) d
  ),
  best AS (
    SELECT p.end_vid FROM paths p WHERE p.node = p.end_vid ORDER BY p.agg_cost LIMIT 1
  ),
  on_path AS (
    SELECT s.group_code, min(s.station_name) AS station_name, min(p.agg_cost) AS t, ST_Centroid(ST_Collect(s.geom)) AS geom
    FROM paths p
    JOIN best b ON b.end_vid = p.end_vid
    JOIN reach.station s ON p.node IN (s.node_a, s.node_b)
    GROUP BY s.group_code
  )
  SELECT (row_number() OVER (ORDER BY o.t))::int, o.group_code, o.station_name,
         round((CASE WHEN o.t = 0 THEN 0 ELSE o.t + p_wait_min END)::numeric, 0), o.geom
  FROM on_path o
  ORDER BY o.t;
END $$;

-- 地図の画面が読む形。駅の計算（pgr_drivingDistance 2回）は1回だけ行い、帯・場所・経路で使い回す。
--   min_total：寄り道をしないで終点に着く時間（起点の t2）。使える時間がこれより短いと、何も返らない
--   bands：過ごせる時間の帯（15・30・60・120分以上）。終点の駅は外す
--   spots：場所ごとに、いちばん長く過ごせる駅と時間。p_min_stay 分以上のものだけ
CREATE OR REPLACE FUNCTION reach.detour_map_json(
  p_from             text,
  p_to               text,
  p_available_min    double precision,
  p_min_stay         double precision DEFAULT 15,
  p_walk_m_per_min   double precision DEFAULT 80,
  p_walk_cap_min     double precision DEFAULT 15,
  p_detour           double precision DEFAULT 1.3
)
RETURNS json LANGUAGE sql STABLE AS $$
  WITH
  st AS MATERIALIZED (
    SELECT * FROM reach.detour_stations(p_from, p_to, p_available_min)
  ),
  route AS MATERIALIZED (
    SELECT r.*, d.slack_min
    FROM reach.detour_route(p_from, p_to) r
    LEFT JOIN st d ON d.group_code = r.group_code
  ),
  bands AS (
    SELECT b AS stay_min, count(*)::int AS station_count,
           ST_Multi(ST_SimplifyPreserveTopology(ST_Union(
             ST_Buffer(st.geom::geography,
                       LEAST((st.slack_min::double precision - b) / 2, p_walk_cap_min) * p_walk_m_per_min / p_detour,
                       'quad_segs=8')::geometry), 0.00005)) AS geom
    FROM unnest('{15,30,60,120}'::double precision[]) AS b
    JOIN st ON st.slack_min > b AND st.role <> 'dest'
    GROUP BY b
  ),
  cand AS (
    SELECT sp.spot_id, sp.source, sp.name, sp.kinds, sp.geom, st.station_name, st.t1_min, st.slack_min,
           ST_Distance(sp.geom::geography, st.geom::geography) * p_detour / p_walk_m_per_min AS walk
    FROM st
    JOIN reach.spot sp
      ON ST_DWithin(sp.geom::geography, st.geom::geography, p_walk_cap_min * p_walk_m_per_min / p_detour)
    WHERE st.role <> 'dest'
  ),
  spots AS (
    SELECT DISTINCT ON (c.spot_id)
           c.spot_id, c.source, c.name, c.kinds, c.geom, c.station_name AS via, c.t1_min,
           round((c.slack_min::double precision - 2 * c.walk)::numeric, 0) AS stay_min,
           round(c.walk::numeric, 0) AS walk_min
    FROM cand c
    WHERE c.slack_min::double precision - 2 * c.walk >= p_min_stay
    ORDER BY c.spot_id, c.slack_min::double precision - 2 * c.walk DESC
  )
  SELECT json_build_object(
    'from', p_from,
    'to', p_to,
    'available_min', p_available_min,
    'min_total', (SELECT t2_min FROM st WHERE role = 'origin' ORDER BY t2_min LIMIT 1),
    'bands', (
      SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
        'type', 'Feature',
        'properties', json_build_object('stay_min', b.stay_min, 'station_count', b.station_count),
        'geometry', ST_AsGeoJSON(b.geom, 6)::json) ORDER BY b.stay_min), '[]'::json))
      FROM bands b
    ),
    'stations', (
      SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
        'type', 'Feature',
        'properties', json_build_object('name', s.station_name, 't1_min', s.t1_min, 't2_min', s.t2_min,
                                        'slack_min', s.slack_min, 'role', s.role, 'lines', s.lines),
        'geometry', ST_AsGeoJSON(s.geom, 6)::json)), '[]'::json))
      FROM st s
    ),
    'spots', (
      SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
        'type', 'Feature',
        'properties', json_build_object('name', sp.name, 'source', sp.source, 'kinds', sp.kinds, 'via', sp.via,
                                        't1_min', sp.t1_min, 'stay_min', sp.stay_min, 'walk_min', sp.walk_min),
        'geometry', ST_AsGeoJSON(sp.geom, 6)::json)), '[]'::json))
      FROM spots sp
    ),
    'route', (
      SELECT coalesce(json_agg(json_build_object(
        'seq', r.seq, 'name', r.station_name, 't1_min', r.t1_min, 'slack_min', r.slack_min,
        'lng', round(ST_X(r.geom)::numeric, 6), 'lat', round(ST_Y(r.geom)::numeric, 6)) ORDER BY r.seq), '[]'::json)
      FROM route r
    )
  )
$$;
