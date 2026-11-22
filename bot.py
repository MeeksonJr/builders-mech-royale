"""MeeksonLabs Mech Royale Tactical Combat & Survival Bot v4.

Key Tactical Principles:
1. Hard Storm Boundary:
   - Storm damage (20 DPS) is fatal. The bot NEVER allows repulsion to push it near the storm perimeter.
   - When distance to center > 50% radius, zone attraction overrides all other forces.
2. Acoustic Stealth & Range Engagement:
   - Gunshots carry 320m. We DO NOT shoot at long range (> 35m), avoiding alerting the hive mind.
   - Inside 35m, return fire aggressively with predictive ballistic lead aiming.
3. Stuck & Obstacle Auto-Escape:
   - Detects low velocity during intended movement.
   - Immediately fires jetpack jump and applies 90-degree lateral tangent slide.
4. Weak-Target Focus & Defensive Upgrades:
   - Focuses lowest HP enemy to eliminate threats and collect power cubes.
   - Prioritizes Armor, Plating, Shields, and Max HP.
"""

from __future__ import annotations

import math
from typing import Optional, List
import mechfight as mf


class Bot:
    name = "MeeksonApexV4"

    def __init__(self, hello: mf.Hello):
        self.pickup_reach = hello["config"]["loot"]["pickup_radius"]
        self.gun_speed = hello["config"]["weapons"]["gun"]["speed"]
        self.stuck_ticks = 0
        self.strafe_sign = 1.0

    def act(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        cur_tick = obs["tick"]
        act: mf.Action = {"tick": cur_tick}

        # -------------------------------------------------------------
        # 1. DEFENSIVE UPGRADE SELECTION
        # -------------------------------------------------------------
        if obs.get("offer"):
            choices = obs["offer"]["choices"]
            best_idx = 0
            best_val = -1000
            for idx, c in enumerate(choices):
                k = c.get("kind", "").lower()
                desc = c.get("desc", "").lower()
                text = f"{k} {desc}"
                if any(w in text for w in ("armor", "plating", "damage reduction", "shield")):
                    val = 150
                elif any(w in text for w in ("health", "max hp", "hp", "regen")):
                    val = 140
                elif any(w in text for w in ("speed", "jet", "energy", "boost")):
                    val = 110
                elif any(w in text for w in ("lightning", "mortar", "laser")):
                    val = 70
                else:
                    val = 50
                if val > best_val:
                    best_val = val
                    best_idx = idx
            act["pick"] = best_idx

        # -------------------------------------------------------------
        # 2. WEAPON PICKUP FOR SECONDARY SLOT
        # -------------------------------------------------------------
        if me["weapons"][1] is None and obs.get("pickups"):
            near_picks = [p for p in obs["pickups"] if math.hypot(p["pos"][0] - x, p["pos"][1] - y) < self.pickup_reach]
            if near_picks:
                pref = {"lightning": 4, "mortar": 3, "laser": 2, "gun": 1}
                chosen = max(near_picks, key=lambda p: pref.get(p.get("kind", ""), 0))
                act["take"] = chosen["id"]

        # -------------------------------------------------------------
        # 3. TARGET EVALUATION
        # -------------------------------------------------------------
        enemies: List[mf.ActorObs] = [m for m in obs.get("mechs", []) if m["kind"] == "mech"]
        damaged_events = [e for e in obs.get("events", []) if e.get("type") == "damaged" and e.get("by") is not None]

        target: Optional[mf.ActorObs] = None

        # Retaliate against immediate attacker
        if damaged_events:
            attacker_id = damaged_events[-1]["by"]
            for em in enemies:
                if em["id"] == attacker_id:
                    target = em
                    break

        # Focus closest or lowest HP enemy within combat range
        if target is None and enemies:
            near_enemies = [m for m in enemies if math.hypot(m["pos"][0] - x, m["pos"][1] - y) < 45.0]
            if near_enemies:
                target = min(near_enemies, key=lambda m: (m.get("hp", 1400), math.hypot(m["pos"][0] - x, m["pos"][1] - y)))

        # -------------------------------------------------------------
        # 4. SAFE ZONE NAVIGATION (ABSOLUTE STORM PRESERVATION)
        # -------------------------------------------------------------
        zone = obs["zone"]
        next_c = zone.get("next_center", zone.get("center", [0.0, 0.0]))
        next_r = zone.get("next_radius", zone.get("radius", 200.0))

        dx_zone = next_c[0] - x
        dy_zone = next_c[1] - y
        dist_to_center = math.hypot(dx_zone, dy_zone)

        if dist_to_center > 0.001:
            zx = dx_zone / dist_to_center
            zy = dy_zone / dist_to_center
        else:
            zx, zy = 0.0, 0.0

        # Strafe perpendicular to target if fighting
        strafe_x, strafe_y = 0.0, 0.0
        if target is not None:
            tx, ty, _ = target["pos"]
            dx_t = tx - x
            dy_t = ty - y
            d_t = math.hypot(dx_t, dy_t)
            if d_t > 0.001:
                ux_t = dx_t / d_t
                uy_t = dy_t / d_t
                strafe_dir = 1.0 if (cur_tick // 40) % 2 == 0 else -1.0
                strafe_x = -uy_t * strafe_dir
                strafe_y = ux_t * strafe_dir

        # Combine zone vector and combat strafe
        if not me.get("in_zone", True) or dist_to_center > (next_r * 0.65):
            # Outside or near boundary: 90% ZONE ATTRACTION, NEVER push outward!
            move_x = zx * 0.90 + strafe_x * 0.10
            move_y = zy * 0.90 + strafe_y * 0.10
        elif target is not None:
            # Safely inside: balanced combat strafe
            move_x = zx * 0.40 + strafe_x * 0.60
            move_y = zy * 0.40 + strafe_y * 0.60
        else:
            # Patrol safely inside ring
            move_x = zx * 0.80
            move_y = zy * 0.80

        # Opportunistic power cube pickup
        if target is None and obs.get("cubes"):
            close_cubes = [c for c in obs["cubes"] if math.hypot(c["pos"][0] - x, c["pos"][1] - y) < 25.0]
            if close_cubes:
                best_c = min(close_cubes, key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                cdx = best_c["pos"][0] - x
                cdy = best_c["pos"][1] - y
                cmag = math.hypot(cdx, cdy)
                if cmag > 0.01:
                    move_x = move_x * 0.7 + (cdx / cmag) * 0.3
                    move_y = move_y * 0.7 + (cdy / cmag) * 0.3

        # -------------------------------------------------------------
        # 5. ANTI-STUCK & OBSTACLE ESCAPE
        # -------------------------------------------------------------
        actual_spd = math.hypot(me["vel"][0], me["vel"][1])
        want_move = math.hypot(move_x, move_y) > 0.3
        if want_move and actual_spd < 1.4 and me.get("grounded", True):
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0

        jump = False
        boost = (not me.get("in_zone", True)) or bool(damaged_events) or (dist_to_center > next_r * 0.75)

        if self.stuck_ticks >= 2:
            # Burst jetpack up and slide 90 degrees tangent to obstacle
            jump = True
            boost = True
            move_x, move_y = -move_y, move_x

        if bool(damaged_events) and me.get("grounded", True) and (cur_tick % 25 == 0):
            # Evasive hopping when taking hits
            jump = True

        move_len = math.hypot(move_x, move_y)
        move_cmd = [move_x / move_len, move_y / move_len] if move_len > 0.001 else [zx, zy]

        # -------------------------------------------------------------
        # 6. FIRING DISCIPLINE & PREDICTIVE AIMING
        # -------------------------------------------------------------
        # Only shoot if target is within 35m (avoids 320m gunshot sound beacon) OR we are attacked
        target_dist = math.hypot(target["pos"][0] - x, target["pos"][1] - y) if target else 999.0
        should_fire = (target is not None) and (target_dist < 36.0 or bool(damaged_events) or obs.get("alive", 16) <= 4)

        if target is not None and should_fire:
            tx, ty, tz = target["pos"]
            vx, vy, vz = target.get("vel", [0.0, 0.0, 0.0])
            t_flight = target_dist / max(100.0, self.gun_speed)

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
            # Silent facing
            face_yaw = math.atan2(move_cmd[1], move_cmd[0])
            act.update(
                move=move_cmd,
                aim=[face_yaw, 0.0],
                fire=[False, False],
                boost=boost,
                jump=jump,
            )

        # Tactical reload during combat lulls
        primary = me["weapons"][0]
        if primary and primary.get("ammo", 12) <= 3 and (target is None or target_dist > 30.0):
            act["reload"] = True

        return act


mf.run(Bot)
