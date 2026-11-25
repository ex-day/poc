-- reach/osmwalk/exit_walk_build.sql
-- exit_walk.sql のあと、出入口の CSV を reach.station_exit に入れてから流す（Issue #46）。

TRUNCATE reach.station_exit_walk, reach.station_spot_walk_exit;

INSERT INTO reach.station_exit_walk (group_code, exit_node, node_id, access_m)
SELECT e.group_code, e.node_id, n.node_id, n.d
FROM reach.station_exit e
JOIN (SELECT DISTINCT group_code FROM reach.station_walk) g USING (group_code)
CROSS JOIN LATERAL (
  SELECT w.node_id, ST_Distance(ST_Transform(w.geom, 4326)::geography, e.geom::geography) AS d
  FROM reach.walk_node w
  ORDER BY w.geom <-> ST_Transform(e.geom, 6668)
  LIMIT 1
) n
WHERE n.d <= 300;
ANALYZE reach.station_exit_walk;

INSERT INTO reach.station_spot_walk_exit (group_code, spot_id, walk_min)
SELECT x.group_code, f.spot_id, min(f.walk_min)
FROM reach.station_exit_walk x
CROSS JOIN LATERAL reach.walk_from_node_ap(x.node_id, x.access_m, 25) f
GROUP BY x.group_code, f.spot_id;
ANALYZE reach.station_spot_walk_exit;
