-- depends: 0014.drone_idle_volume_automation

-- Presence for the always-on Edge connection: which Drones currently hold a live
-- persistent outbound mux to the Edge, on which Edge node, and the reflexive
-- (NAT-observed) source address the Edge saw. Stored alongside public
-- reachability in drone_network_state and written via the lean
-- update_device_edge_presence path (never the full-state mirror), so it cannot
-- clobber columns owned by other writers.

ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS edge_online BOOLEAN;
ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS edge_node TEXT;
ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS reflexive_endpoint TEXT;
ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS edge_connected_at TIMESTAMPTZ;
