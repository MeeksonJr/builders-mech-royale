"""MeeksonLabs Mech Royale Tactical Combat & Survival Bot.

Tactics:
  1. Relentless target-leading accuracy: computes exact projectile travel time (speed ~252 m/s)
     to lead enemy velocity vectors for devastating hit rates.
  2. Combat strafing: moves perpendicular to the primary target while maintaining forward progress
     toward the safe zone center, throwing off incoming enemy fire.
  3. Zone dominance: prioritizes entering and holding the safe zone (boosts immediately when outside).
  4. Reactive threat targeting: immediately pivots and retaliates against the specific enemy that damaged us.
  5. Secondary weapon collection & upgrade selection for maximum survivability.
"""

from __future__ import annotations

import math
import mechfight as mf


class Bot:
    name = "MeeksonCombat"

    def __init__(self, hello: mf.Hello):
        self.reach = hello["config"]["loot"]["pickup_radius"]
        self.gun_speed = hello["config"]["weapons"]["gun"]["speed"]

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

        # 3. Identify primary target
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
            # Target closest enemy
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
            # Compute strafe vector perpendicular to target
            tx, ty, _ = target["pos"]
            dx_t = tx - x
            dy_t = ty - y
            d_t = math.hypot(dx_t, dy_t)
            if d_t > 0.001:
                ux_t = dx_t / d_t
                uy_t = dy_t / d_t
                # Alternating strafe based on tick (reverses every 3 seconds to avoid running into walls)
                strafe_dir = 1.0 if (obs["tick"] // 60) % 2 == 0 else -1.0
                strafe_x = -uy_t * strafe_dir
                strafe_y = ux_t * strafe_dir
            else:
                strafe_x, strafe_y = 0.0, 0.0

            # Blend: 60% toward zone, 40% strafe
            if not me["in_zone"] or dist_to_center > zone["next_radius"]:
                # Outside zone: 90% toward zone!
                move_x = zx * 0.9 + strafe_x * 0.1
                move_y = zy * 0.9 + strafe_y * 0.1
            else:
                move_x = zx * 0.5 + strafe_x * 0.5
                move_y = zy * 0.5 + strafe_y * 0.5
        else:
            move_x, move_y = zx, zy

        move_len = math.hypot(move_x, move_y)
        if move_len > 0.001:
            move_cmd = [move_x / move_len, move_y / move_len]
        else:
            move_cmd = [zx, zy]

        # Boost when out of zone or taking damage
        is_damaged = bool(damaged_events)
        boost = (not me["in_zone"]) or is_damaged or (dist_to_center > zone["next_radius"])
        jump = is_damaged and me["grounded"] and (obs["tick"] % 30 == 0)

        # 5. Aiming & Ballistic Lead Firing
        if target is not None:
            tx, ty, tz = target["pos"]
            vx, vy, vz = target.get("vel", [0.0, 0.0, 0.0])
            t_dist = math.dist(target["pos"], me["pos"])
            t_flight = t_dist / max(100.0, self.gun_speed)

            # Predictive lead
            lead_x = tx + vx * t_flight
            lead_y = ty + vy * t_flight
            lead_z = tz + 4.5 + vz * t_flight  # Target center of mass (height 4.5)

            diff_x = lead_x - x
            diff_y = lead_y - y
            diff_z = lead_z - (z + 7.0)  # Muzzle height is 7.0

            aim_yaw = math.atan2(diff_y, diff_x)
            aim_pitch = math.atan2(diff_z, math.hypot(diff_x, diff_y))

            # Fire both primary and secondary weapons continuously at target!
            act.update(
                move=move_cmd,
                aim=[aim_yaw, aim_pitch],
                fire=[True, True],
                boost=boost,
                jump=jump,
            )
            mf.draw.line([x, y, z + 7.0], [lead_x, lead_y, lead_z], "#ff2200")
        else:
            # Face forward
            yaw = math.atan2(move_cmd[1], move_cmd[0])
            act.update(
                move=move_cmd,
                aim=[yaw, 0.0],
                fire=[False, False],
                boost=boost,
                jump=jump,
            )

        return act


mf.run(Bot)
