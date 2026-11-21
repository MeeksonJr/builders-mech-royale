"""MeeksonLabs Mech Royale Survival Bot.

Strategy:
  1. Placement-first objective (Placement = 16 - place; kills award 0 points).
  2. Hive-mind evasion: The 15 house bots share vision and focus on the weakest visible target.
     Holding fire and breaking line of sight prevents drawing the squad's aggro.
  3. Zone perimeter positioning: Navigates using potential fields to stay on the safe inner rim
     of the contracting circle rather than standing in the exposed dead center.
  4. Defensive upgrade preference: Prioritizes armor, max HP, and speed/energy over weapon upgrades.
  5. Reactive evasion: Boosts and jumps perpendicular to incoming fire upon taking damage.
  6. Endgame execution: Transitions to aggressive firing when 4 or fewer mechs remain.
"""

from __future__ import annotations

import math
import mechfight as mf


class Bot:
    name = "MeeksonSurvival"

    def __init__(self, hello: mf.Hello):
        self.reach = hello["config"]["loot"]["pickup_radius"]
        self.gun_speed = hello["config"]["weapons"]["gun"]["speed"]

    def act(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        act: mf.Action = {"tick": obs["tick"]}

        # 1. Automatic upgrade selection (prefer survivability)
        if obs["offer"]:
            choices = obs["offer"]["choices"]
            best_choice = 0
            best_score = -100
            for idx, c in enumerate(choices):
                kind = c.get("kind", "").lower()
                # Score defensive perks highest
                if any(k in kind for k in ("armor", "hp", "health", "shield", "resist")):
                    score = 100
                elif any(k in kind for k in ("speed", "jet", "energy", "boost", "dash")):
                    score = 80
                elif any(k in kind for k in ("lightning", "mortar", "laser")):
                    score = 60
                else:
                    score = 40
                if score > best_score:
                    best_score = score
                    best_choice = idx
            act["pick"] = best_choice

        # 2. Weapon pickup for empty slot
        near_pickups = [p for p in obs["pickups"] if math.dist(p["pos"][:2], (x, y)) < self.reach]
        if me["weapons"][1] is None and near_pickups:
            # Prefer heavy secondary: lightning > mortar > laser > gun
            pref = {"lightning": 4, "mortar": 3, "laser": 2, "gun": 1}
            best_p = max(near_pickups, key=lambda p: pref.get(p.get("kind", ""), 0))
            act["take"] = best_p["id"]

        # 3. Zone Navigation & Potential Field Movement
        zone = obs["zone"]
        next_c = zone["next_center"]
        next_r = zone["next_radius"]

        dx_zone = next_c[0] - x
        dy_zone = next_c[1] - y
        dist_to_center = math.hypot(dx_zone, dy_zone)

        # Repulsion from visible enemies
        enemies = [m for m in obs["mechs"] if m["kind"] == "mech"]
        repel_x = 0.0
        repel_y = 0.0
        for em in enemies:
            ex, ey, ez = em["pos"]
            edist = math.hypot(x - ex, y - ey)
            if edist < 60.0 and edist > 0.001:
                weight = 1500.0 / (edist * edist)
                repel_x += ((x - ex) / edist) * weight
                repel_y += ((y - ey) / edist) * weight

        # Combine zone attraction + enemy repulsion
        # If we are comfortably within 70% of safe radius, zone pull is gentle
        safe_margin = max(15.0, next_r * 0.75)
        if dist_to_center > safe_margin:
            zone_weight = 1.0 + (dist_to_center - safe_margin) / 20.0
        else:
            zone_weight = 0.2

        if dist_to_center > 0.001:
            norm_zx = dx_zone / dist_to_center
            norm_zy = dy_zone / dist_to_center
        else:
            norm_zx, norm_zy = 0.0, 0.0

        move_x = norm_zx * zone_weight + repel_x
        move_y = norm_zy * zone_weight + repel_y
        move_len = math.hypot(move_x, move_y)
        if move_len > 0.001:
            move_cmd = [move_x / move_len, move_y / move_len]
        else:
            move_cmd = [norm_zx, norm_zy]

        # 4. Evasion & Mobility
        was_damaged = any(e.get("type") == "damaged" for e in obs.get("events", []))
        should_boost = (not me["in_zone"]) or was_damaged or (dist_to_center > next_r)
        should_jump = was_damaged and me["grounded"] and (obs["tick"] % 15 == 0)

        act.update(move=move_cmd, boost=should_boost, jump=should_jump)

        # 5. Aiming and Fire Discipline
        # In endgame (alive <= 4) or when under direct attack (< 28m), fire back!
        alive = obs["alive"]
        closest_enemy = min(enemies, key=lambda m: math.dist(m["pos"], me["pos"])) if enemies else None
        closest_dist = math.dist(closest_enemy["pos"], me["pos"]) if closest_enemy else 9999.0

        should_fire = (alive <= 4) or (closest_dist < 28.0) or (was_damaged and closest_dist < 45.0)

        if closest_enemy and should_fire:
            tx, ty, tz = closest_enemy["pos"]
            vx, vy, vz = closest_enemy.get("vel", [0, 0, 0])
            t_flight = closest_dist / max(50.0, self.gun_speed)
            lead_x = tx + vx * t_flight
            lead_y = ty + vy * t_flight
            lead_z = tz + 4.5 + vz * t_flight

            aim_yaw = math.atan2(lead_y - y, lead_x - x)
            aim_pitch = math.atan2(lead_z - (z + 7.0), math.hypot(lead_x - x, lead_y - y))
            act.update(aim=[aim_yaw, aim_pitch], fire=[True, True])
            mf.draw.line([x, y, z + 7.0], [lead_x, lead_y, lead_z], "#00ffcc")
        else:
            # Look in direction of motion to watch for incoming threats
            face_yaw = math.atan2(move_cmd[1], move_cmd[0])
            act.update(aim=[face_yaw, 0.0], fire=[False, False])

        # Debug overlay
        mf.draw.point([next_c[0], next_c[1], z], "#00ff88", 6)
        mf.draw.label(f"HP {me['hp']:.0f} | Alive {alive} | Dist {dist_to_center:.0f}m")
        return act


mf.run(Bot)
