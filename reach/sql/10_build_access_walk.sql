-- reach/sql/10_build_access_walk.sql
-- reach.spot_access_point を道路の点に結び付け、駅グループからの歩き（到達点の版）を前もって計算する（Issue #31）。
-- 07・08 のあと、spot_access_point を入れてから流す。駅グループの数だけ道路のグラフをたどるので、数分〜十数分かかる。

TRUNCATE reach.station_spot_walk_ap, reach.spot_walk_ap;

-- 到達点から、いちばん近い道路の点（07 の場所と同じく 300m まで）
INSERT INTO reach.spot_walk_ap (spot_id, ap_id, node_id, access_m)
SELECT a.spot_id, a.ap_id, n.node_id, n.d
FROM reach.spot_access_point a
CROSS JOIN LATERAL (
  SELECT w.node_id, ST_Distance(ST_Transform(w.geom, 4326)::geography, a.geom::geography) AS d
  FROM reach.walk_node w
  ORDER BY w.geom <-> ST_Transform(a.geom, 6668)
  LIMIT 1
) n
WHERE n.d <= 300;

-- 到達点の無い場所（または、どの到達点も道路に結び付かなかった場所）は、今の点のまま
INSERT INTO reach.spot_walk_ap (spot_id, ap_id, node_id, access_m)
SELECT sw.spot_id, NULL, sw.node_id, sw.access_m
FROM reach.spot_walk sw
WHERE NOT EXISTS (SELECT 1 FROM reach.spot_walk_ap x WHERE x.spot_id = sw.spot_id);

ANALYZE reach.spot_walk_ap;

INSERT INTO reach.station_spot_walk_ap (group_code, spot_id, walk_min)
SELECT sw.group_code, f.spot_id, f.walk_min
FROM reach.station_walk sw
CROSS JOIN LATERAL reach.walk_from_station_ap(sw.group_code, 25) f;
ANALYZE reach.station_spot_walk_ap;
