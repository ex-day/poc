-- reach/osmwalk/walk_precompute.sql
-- 駅グループから片道25分までに歩いて行ける場所を前もって計算する。08_walk_suggest.sql の前半（reach.walk_from_station と
-- reach.station_spot_walk の計算）と同じ中身で、提案の関数（埋め込みを使う）を除いたもの（Issue #46）。
-- N13 用の DB（07 のあと）と OSM 用の DB（build_osm.sql のあと）の両方に流す。

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

TRUNCATE reach.station_spot_walk;
INSERT INTO reach.station_spot_walk (group_code, spot_id, walk_min)
SELECT sw.group_code, f.spot_id, f.walk_min
FROM reach.station_walk sw
CROSS JOIN LATERAL reach.walk_from_station(sw.group_code, 25) f;
ANALYZE reach.station_spot_walk;
