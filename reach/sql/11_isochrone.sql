-- reach/sql/11_isochrone.sql
-- 到達圏を「時間の帯の面」にして地図に重ねる（Issue #40、ex-day/platform#134）。
-- 前提：01〜03（鉄道のグラフと reach.edges_from）、04 と load_spots.py（reach.spot）。
--
--   SELECT * FROM reach.reach_from('横浜', 60);                 -- 片道で60分以内に行ける駅と分数
--   SELECT band_min, ST_Area(geom::geography) FROM reach.isochrone_bands('横浜');
--   SELECT reach.reach_map_json('横浜', 60);                    -- 地図の画面（reach/map/）が読む GeoJSON
--
-- 面の作り方：駅ごとに「その帯の時間から、駅に着くまでの分数を引いた残り」で歩ける半径の円を作り、
-- 帯ごとに合体（ST_Union）する。歩きは直線の距離で、道のりの遠回りの分を割り引く（行けない側に寄せる）。
-- 本物の等時間線ではない（駅のまわりの道のりは測らない。#23 の歩きのグラフは使わない）。

-- 起点の駅の点。同じ名前の駅のグループが複数あっても、すべてを起点にする（片道の起点なので、どれから乗ってもよい）。
-- reach.group_nodes（03）は同じ名前のグループが複数あるとエラーにするので、別に作る。
CREATE OR REPLACE FUNCTION reach.name_nodes(p_station text)
RETURNS bigint[] LANGUAGE sql STABLE AS $$
  SELECT array_agg(DISTINCT n)
  FROM reach.station s, unnest(ARRAY[s.node_a, s.node_b]) n
  WHERE (s.station_name = p_station OR s.group_code = p_station) AND n IS NOT NULL
$$;

-- 片道の到達圏：起点から p_max_min 分以内に行ける駅（駅グループ単位）と分数。
-- 分数 ＝ 乗り始めの待ち時間 ＋ グラフの所要時間。起点の駅そのものは 0分。
CREATE OR REPLACE FUNCTION reach.reach_from(
  p_from      text,
  p_max_min   double precision,
  p_wait_min  double precision DEFAULT 5
)
RETURNS TABLE (group_code text, station_name text, lines text, t_min numeric, geom geometry)
LANGUAGE plpgsql STABLE AS $$
#variable_conflict use_column
DECLARE
  nodes bigint[] := reach.name_nodes(p_from);
BEGIN
  IF nodes IS NULL THEN
    RAISE EXCEPTION '駅が見つからない：%', p_from;
  END IF;
  RETURN QUERY
  WITH
  origin AS (
    SELECT DISTINCT s.group_code FROM reach.station s
    WHERE s.station_name = p_from OR s.group_code = p_from
  ),
  dd AS (
    SELECT d.node, min(d.agg_cost) AS t
    FROM pgr_drivingDistance(reach.edges_from(nodes), 0, GREATEST(p_max_min - p_wait_min, 0), directed := true) d
    GROUP BY d.node
  ),
  per_station AS (
    SELECT s.group_code, s.station_name, l.operator || ' ' || l.line_name AS line,
           CASE WHEN s.group_code IN (SELECT o.group_code FROM origin o) THEN 0
                ELSE LEAST(fa.t, fb.t) + p_wait_min END AS t,
           s.geom
    FROM reach.station s
    JOIN reach.line l USING (line_id)
    LEFT JOIN dd fa ON fa.node = s.node_a
    LEFT JOIN dd fb ON fb.node = s.node_b
  )
  SELECT ps.group_code, min(ps.station_name), string_agg(DISTINCT ps.line, '、'),
         round(min(ps.t)::numeric, 0), ST_Centroid(ST_Collect(ps.geom))
  FROM per_station ps
  WHERE ps.t IS NOT NULL AND ps.t <= p_max_min
  GROUP BY ps.group_code;
END $$;

-- 時間の帯の面。帯ごとに1行（MultiPolygon、WGS84）。外側の帯は内側を含む（描くときは外側から重ねる）。
CREATE OR REPLACE FUNCTION reach.isochrone_bands(
  p_from             text,
  p_bands            double precision[] DEFAULT '{15,30,45,60}',
  p_walk_m_per_min   double precision DEFAULT 80,    -- 歩く速さ（不動産の表示の「1分＝80m」）
  p_walk_cap_min     double precision DEFAULT 15,    -- 駅から歩く時間の上限
  p_detour           double precision DEFAULT 1.3,   -- 直線の距離に対する道のりの遠回りの比（大きいほど半径が小さい）
  p_wait_min         double precision DEFAULT 5
)
RETURNS TABLE (band_min double precision, station_count int, geom geometry)
LANGUAGE sql STABLE AS $$
  WITH st AS (
    SELECT * FROM reach.reach_from(p_from, (SELECT max(b) FROM unnest(p_bands) b), p_wait_min)
  )
  SELECT b,
         count(*)::int,
         ST_Multi(ST_SimplifyPreserveTopology(ST_Union(
           ST_Buffer(st.geom::geography,
                     LEAST(b - st.t_min::double precision, p_walk_cap_min) * p_walk_m_per_min / p_detour,
                     'quad_segs=8')::geometry), 0.00005))
  FROM unnest(p_bands) AS b
  JOIN st ON st.t_min < b
  GROUP BY b
  ORDER BY b;
$$;

-- 到達圏の中の場所（reach.spot）と、いちばん早く着く時間・そのときの駅。
CREATE OR REPLACE FUNCTION reach.spots_in_reach(
  p_from             text,
  p_max_min          double precision,
  p_walk_m_per_min   double precision DEFAULT 80,
  p_walk_cap_min     double precision DEFAULT 15,
  p_detour           double precision DEFAULT 1.3,
  p_wait_min         double precision DEFAULT 5
)
RETURNS TABLE (spot_id int, source text, name text, kinds text, t_min numeric, via_station text, walk_min numeric, geom geometry)
LANGUAGE sql STABLE AS $$
  WITH st AS (
    SELECT * FROM reach.reach_from(p_from, p_max_min, p_wait_min)
  ),
  cand AS (
    SELECT sp.spot_id, sp.source, sp.name, sp.kinds, sp.geom, st.station_name,
           ST_Distance(sp.geom::geography, st.geom::geography) * p_detour / p_walk_m_per_min AS walk,
           st.t_min::double precision AS t_station
    FROM st
    JOIN reach.spot sp
      ON ST_DWithin(sp.geom::geography, st.geom::geography, p_walk_cap_min * p_walk_m_per_min / p_detour)
  )
  SELECT DISTINCT ON (c.spot_id)
         c.spot_id, c.source, c.name, c.kinds,
         round((c.t_station + c.walk)::numeric, 0), c.station_name, round(c.walk::numeric, 0), c.geom
  FROM cand c
  WHERE c.t_station + c.walk <= p_max_min
  ORDER BY c.spot_id, c.t_station + c.walk;
$$;

-- 地図の画面が読む形：帯の面・駅・場所を、それぞれ GeoJSON の FeatureCollection にまとめる。
CREATE OR REPLACE FUNCTION reach.reach_map_json(p_from text, p_max_min double precision DEFAULT 60)
RETURNS json LANGUAGE sql STABLE AS $$
  SELECT json_build_object(
    'from', p_from,
    'max_min', p_max_min,
    'bands', (
      SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
        'type', 'Feature',
        'properties', json_build_object('band_min', b.band_min, 'station_count', b.station_count),
        'geometry', ST_AsGeoJSON(b.geom, 6)::json) ORDER BY b.band_min DESC), '[]'::json))
      FROM reach.isochrone_bands(p_from, ARRAY(SELECT x FROM unnest('{15,30,45,60}'::double precision[]) x WHERE x <= p_max_min)) b
    ),
    'stations', (
      SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
        'type', 'Feature',
        'properties', json_build_object('name', s.station_name, 't_min', s.t_min, 'lines', s.lines),
        'geometry', ST_AsGeoJSON(s.geom, 6)::json)), '[]'::json))
      FROM reach.reach_from(p_from, p_max_min) s
    ),
    'spots', (
      SELECT json_build_object('type', 'FeatureCollection', 'features', coalesce(json_agg(json_build_object(
        'type', 'Feature',
        'properties', json_build_object('name', sp.name, 'source', sp.source, 'kinds', sp.kinds,
                                        't_min', sp.t_min, 'via', sp.via_station, 'walk_min', sp.walk_min),
        'geometry', ST_AsGeoJSON(sp.geom, 6)::json)), '[]'::json))
      FROM reach.spots_in_reach(p_from, p_max_min) sp
    )
  )
$$;
