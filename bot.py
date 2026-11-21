"""MeeksonLabs Mech Royale Tactical Combat & Survival Bot v2.

Enhancements:
  1. Stuck & Obstacle Auto-Escape: Detects when blocked by buildings/barriers and immediately
     activates jetpack climb and 90-degree tangent slide.
  2. Weak-Target Focus: Prioritizes eliminating low-HP enemies to secure power cubes and reduce threats.
  3. Tactical Reload: Reloads primary gun during lulls in combat so full magazine is ready for firefights.
  4. Predictive Lead & Dual-Weapon Fire: Targets center-of-mass with full ballistic velocity leading.
  5. Safe-Zone Dominance: Automatically sprints and jets to secure zone center when circle contracts.
"""

from __future__ import annotations

import math
import mechfight as mf


class Bot:
    name = "MeeksonCombatV2"

    def __init__(self, hello: mf.Hello):
        self.reach = hello["config"]["loot"]["pickup_radius"]
        self.gun_speed = hello["config"]["weapons"]["gun"]["speed"]
        self.stuck_ticks = 0

    def act(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        act: mf.Action = {"tick": obs["tick"]}

        # 1. Automatic upgrade selection (prefer armor/hp/speed)
        if obs["offer"]:
            choices = obs["offer"]["choices"]
            best_choice = 0
            best_score = -100
            for idx, c in enumerate(choices):
                kind = c.get("kind", "").lower()
                if any(k in kind for k in ("armor", "hp", "health", "shield")):
                    score = 100
                elif any(k in kind for k in ("speed", "jet", "energy", "boost")):
                    score = 80
                elif any(k in kind for k in ("lightning", "mortar", "laser")):
                    score = 60
                else:
                    score = 40
                if score > best_score:
                    best_score = score
                    best_choice = idx
            act["pick"] = best_choice

        # 2. Weapon pickup for empty secondary slot
        near_pickups = [p for p in obs["pickups"] if math.dist(p["pos"][:2], (x, y)) < self.reach]
        if me["weapons"][1] is None and near_pickups:
            pref = {"lightning": 4, "mortar": 3, "laser": 2, "gun": 1}
            best_p = max(near_pickups, key=lambda p: pref.get(p.get("kind", ""), 0))
            act["take"] = best_p["id"]

        # 3. Identify primary target (retaliate against attackers, or focus lowest-HP near enemy)
        enemies = [m for m in obs["mechs"] if m["kind"] == "mech"]
        damaged_events = [e for e in obs.get("events", []) if e.get("type") == "damaged" and e.get("by") is not None]

        target = None
        if damaged_events:
            attacker_id = damaged_events[-1]["by"]
            for em in enemies:
                if em["id"] == attacker_id:
                    target = em
                    break

        if target is None and enemies:
            near_enemies = [m for m in enemies if math.dist(m["pos"], me["pos"]) < 95.0]
            if near_enemies:
                # Target lowest HP enemy within engagement range
                target = min(near_enemies, key=lambda m: (m.get("hp", 1400), math.dist(m["pos"], me["pos"])))
            else:
                target = min(enemies, key=lambda m: math.dist(m["pos"], me["pos"]))

        # 4. Movement: Safe Zone Vector + Combat Strafe
        zone = obs["zone"]
        next_c = zone["next_center"]
        dx_zone = next_c[0] - x
        dy_zone = next_c[1] - y
        dist_to_center = math.hypot(dx_zone, dy_zone)

        if dist_to_center > 0.001:
            zx = dx_zone / dist_to_center
            zy = dy_zone / dist_to_center
        else:
            zx, zy = 0.0, 0.0

        if target is not None:
            tx, ty, _ = target["pos"]
            dx_t = tx - x
            dy_t = ty - y
            d_t = math.hypot(dx_t, dy_t)
            if d_t > 0.001:
                ux_t = dx_t / d_t
                uy_t = dy_t / d_t
                strafe_dir = 1.0 if (obs["tick"] // 50) % 2 == 0 else -1.0
                strafe_x = -uy_t * strafe_dir
                strafe_y = ux_t * strafe_dir
            else:
                strafe_x, strafe_y = 0.0, 0.0

            if not me["in_zone"] or dist_to_center > zone["next_radius"]:
                move_x = zx * 0.85 + strafe_x * 0.15
                move_y = zy * 0.85 + strafe_y * 0.15
            else:
                move_x = zx * 0.5 + strafe_x * 0.5
                move_y = zy * 0.5 + strafe_y * 0.5
        else:
            move_x, move_y = zx, zy

        # 5. Stuck & Obstacle Detection (anti-wall trapping)
        actual_spd = math.hypot(me["vel"][0], me["vel"][1])
        want_move = math.hypot(move_x, move_y) > 0.4
        if want_move and actual_spd < 1.6 and me["grounded"]:
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0

        # Default mobility
        is_damaged = bool(damaged_events)
        boost = (not me["in_zone"]) or is_damaged or (dist_to_center > zone["next_radius"])
        jump = (is_damaged and me["grounded"] and (obs["tick"] % 30 == 0))

        # Jet escape maneuver if stuck against building
        if self.stuck_ticks >= 3:
            jump = True
            boost = True
            # Slide tangent to obstacle
            move_x, move_y = -move_y, move_x

        move_len = math.hypot(move_x, move_y)
        move_cmd = [move_x / move_len, move_y / move_len] if move_len > 0.001 else [zx, zy]

        # 6. Aiming & Ballistic Lead Firing
        if target is not None:
            tx, ty, tz = target["pos"]
            vx, vy, vz = target.get("vel", [0.0, 0.0, 0.0])
            t_dist = math.dist(target["pos"], me["pos"])
            t_flight = t_dist / max(100.0, self.gun_speed)

            lead_x = tx + vx * t_flight
            lead_y = ty + vy * t_flight
            lead_z = tz + 4.5 + vz * t_flight

            diff_x = lead_x - x
            diff_y = lead_y - y
            diff_z = lead_z - (z + 7.0)

            aim_yaw = math.atan2(diff_y, diff_x)
            aim_pitch = math.atan2(diff_z, math.hypot(diff_x, diff_y))

            act.update(
                move=move_cmd,
                aim=[aim_yaw, aim_pitch],
                fire=[True, True],
                boost=boost,
                jump=jump,
            )
            mf.draw.line([x, y, z + 7.0], [lead_x, lead_y, lead_z], "#ff2200")
        else:
            yaw = math.atan2(move_cmd[1], move_cmd[0])
            act.update(
                move=move_cmd,
                aim=[yaw, 0.0],
                fire=[False, False],
                boost=boost,
                jump=jump,
            )

        # 7. Tactical Early Reload
        primary = me["weapons"][0]
        if primary and primary.get("ammo", 12) <= 3 and (not target or math.dist(target["pos"], me["pos"]) > 45.0):
            act["reload"] = True

        return act


mf.run(Bot)
