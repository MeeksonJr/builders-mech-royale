"""MeeksonLabs Mech Royale - Tactical Stealth & Survival Specialist Bot (v3).

Designed for the Builders Mech Royale tournament:
1. Strict Fire Discipline:
   - Gunfire has a 320m acoustic radius. Firing attracts 15 coordinated house bots.
   - We maintain total silence unless in point-blank self-defense (< 20m) or endgame (alive <= 3).
2. Zone Perimeter Hugging:
   - Avoids the meat-grinder center where bots brawl.
   - Orbits at 60-75% of the safe-zone radius, smoothly migrating ahead of storm shrink.
3. Repulsion & Threat Avoidance:
   - Actively calculates repulsion forces from all visible enemies and nearby gun sounds.
   - Breaks line-of-sight and retreats behind terrain.
4. Anti-Stuck Jetpack Navigation:
   - Detects low velocity during intended motion.
   - Instantly applies 90-degree tangent slides and vertical jetpack climbing.
5. Defensive Upgrade Hierarchy:
   - Armor, Max HP, and Shields prioritized above all else to maximize survival time.
"""

from __future__ import annotations

import math
from typing import Optional, List, Tuple
import mechfight as mf


class Bot:
    name = "MeeksonStealthV3"

    def __init__(self, hello: mf.Hello):
        self.pickup_reach = hello["config"]["loot"]["pickup_radius"]
        self.gun_speed = hello["config"]["weapons"]["gun"]["speed"]
        self.stuck_ticks = 0
        self.last_damaged_tick = -100
        self.last_attacker_id: Optional[int] = None
        self.strafe_sign = 1.0

    def act(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        cur_tick = obs["tick"]
        alive_count = obs.get("alive", 16)
        act: mf.Action = {"tick": cur_tick}

        # -------------------------------------------------------------
        # 1. UPGRADE SELECTION (Defensive Priority)
        # -------------------------------------------------------------
        if obs.get("offer"):
            choices = obs["offer"]["choices"]
            best_idx = 0
            best_val = -1000
            for idx, c in enumerate(choices):
                k = c.get("kind", "").lower()
                desc = c.get("desc", "").lower()
                text = f"{k} {desc}"
                # Defense & Survival > Mobility > Attack
                if any(w in text for w in ("armor", "plating", "damage reduction", "shield")):
                    val = 150
                elif any(w in text for w in ("health", "max hp", "hp", "regen")):
                    val = 140
                elif any(w in text for w in ("speed", "jet", "energy", "boost", "thrust")):
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
        # 2. WEAPON PICKUP (Opportunistic secondary slot fill)
        # -------------------------------------------------------------
        if me["weapons"][1] is None and obs.get("pickups"):
            near_picks = [p for p in obs["pickups"] if math.hypot(p["pos"][0] - x, p["pos"][1] - y) < self.pickup_reach]
            if near_picks:
                pref = {"lightning": 4, "mortar": 3, "laser": 2, "gun": 1}
                chosen = max(near_picks, key=lambda p: pref.get(p.get("kind", ""), 0))
                act["take"] = chosen["id"]

        # -------------------------------------------------------------
        # 3. THREAT EVALUATION & DAMAGE TRACKING
        # -------------------------------------------------------------
        events = obs.get("events", [])
        for e in events:
            if e.get("type") == "damaged":
                self.last_damaged_tick = cur_tick
                if e.get("by") is not None:
                    self.last_attacker_id = e["by"]

        is_under_attack = (cur_tick - self.last_damaged_tick) < 30  # within last 1.5 seconds

        enemies: List[mf.ActorObs] = [m for m in obs.get("mechs", []) if m["kind"] == "mech"]

        # Find closest enemy and attacker
        closest_enemy: Optional[mf.ActorObs] = None
        min_enemy_dist = 9999.0
        attacker_enemy: Optional[mf.ActorObs] = None

        for em in enemies:
            d = math.hypot(em["pos"][0] - x, em["pos"][1] - y)
            if d < min_enemy_dist:
                min_enemy_dist = d
                closest_enemy = em
            if self.last_attacker_id is not None and em["id"] == self.last_attacker_id:
                attacker_enemy = em

        # -------------------------------------------------------------
        # 4. VECTOR FIELD NAVIGATION
        # -------------------------------------------------------------
        # Target zone dynamics
        zone = obs["zone"]
        next_c = zone.get("next_center", zone.get("center", [0.0, 0.0]))
        next_r = zone.get("next_radius", zone.get("radius", 200.0))
        cur_c = zone.get("center", [0.0, 0.0])
        cur_r = zone.get("radius", 300.0)

        # Vector from current position to next zone center
        dx_to_center = next_c[0] - x
        dy_to_center = next_c[1] - y
        dist_to_center = math.hypot(dx_to_center, dy_to_center)

        # Desired perimeter radius (stay around 60% of next_radius to avoid center death match)
        target_ring_r = max(20.0, next_r * 0.55)

        # Zone Force
        if dist_to_center > 0.01:
            ucx = dx_to_center / dist_to_center
            ucy = dy_to_center / dist_to_center
        else:
            ucx, ucy = 0.0, 0.0

        zone_weight = 1.0
        if not me.get("in_zone", True) or dist_to_center > next_r * 0.85:
            # Urgent: outside or close to storm edge, pull strongly inward
            zone_fx = ucx * 3.5
            zone_fy = ucy * 3.5
        elif dist_to_center < target_ring_r * 0.4:
            # Too close to center brawl: gently push outwards towards safe ring
            zone_fx = -ucx * 0.8
            zone_fy = -ucy * 0.8
        else:
            # In good ring, maintain position or orbit
            tangent_x = -ucy * self.strafe_sign
            tangent_y = ucx * self.strafe_sign
            pull = (dist_to_center - target_ring_r) / max(1.0, target_ring_r)
            zone_fx = ucx * pull + tangent_x * 0.6
            zone_fy = ucy * pull + tangent_y * 0.6

        # Enemy Repulsion Force
        repulse_fx = 0.0
        repulse_fy = 0.0
        for em in enemies:
            edx = x - em["pos"][0]
            edy = y - em["pos"][1]
            edist = math.hypot(edx, edy)
            if edist < 80.0:
                if edist < 0.01:
                    edist = 0.01
                    edx = 1.0
                    edy = 0.0
                strength = ((80.0 - edist) / 80.0) ** 2 * 3.0
                repulse_fx += (edx / edist) * strength
                repulse_fy += (edy / edist) * strength

        # Sound Repulsion Force (Flee from nearby firefights)
        for snd in obs.get("sounds", []):
            if snd.get("dist") in ("near", "mid") and snd.get("age", 0) < 0.2:
                bearing = snd.get("bearing", 0.0)
                # Opposite to bearing
                flee_x = -math.cos(bearing)
                flee_y = -math.sin(bearing)
                s_weight = 1.2 if snd.get("dist") == "near" else 0.5
                repulse_fx += flee_x * s_weight
                repulse_fy += flee_y * s_weight

        # Cube Attraction (Opportunistic looting if safe)
        cube_fx = 0.0
        cube_fy = 0.0
        if obs.get("cubes") and not is_under_attack:
            closest_cube = None
            closest_cube_d = 45.0
            for c in obs["cubes"]:
                cd = math.hypot(c["pos"][0] - x, c["pos"][1] - y)
                if cd < closest_cube_d:
                    # Make sure no enemy is camping it
                    safe = True
                    for em in enemies:
                        if math.hypot(c["pos"][0] - em["pos"][0], c["pos"][1] - em["pos"][1]) < 25.0:
                            safe = False
                            break
                    if safe:
                        closest_cube_d = cd
                        closest_cube = c
            if closest_cube:
                cdx = closest_cube["pos"][0] - x
                cdy = closest_cube["pos"][1] - y
                c_mag = math.hypot(cdx, cdy)
                if c_mag > 0.01:
                    cube_fx = (cdx / c_mag) * 0.9
                    cube_fy = (cdy / c_mag) * 0.9

        # Combine Forces
        move_fx = zone_fx + repulse_fx + cube_fx
        move_fy = zone_fy + repulse_fy + cube_fy

        # -------------------------------------------------------------
        # 5. ANTI-STUCK & WALL DETECTOR
        # -------------------------------------------------------------
        speed = math.hypot(me["vel"][0], me["vel"][1])
        want_to_move = math.hypot(move_fx, move_fy) > 0.3
        if want_to_move and speed < 1.3 and me.get("grounded", True):
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0

        jump = False
        boost = False

        if self.stuck_ticks >= 2:
            # Jetpack up and slide 90 degrees tangent to obstacle
            jump = True
            boost = True
            self.strafe_sign *= -1.0
            move_fx, move_fy = -move_fy, move_fx
            if self.stuck_ticks >= 5:
                # Randomize slightly to break complex corner pinch
                move_fx += math.sin(cur_tick) * 2.0
                move_fy += math.cos(cur_tick) * 2.0

        # Mobility triggers
        if not me.get("in_zone", True) or is_under_attack or min_enemy_dist < 35.0:
            boost = True
        if is_under_attack and me.get("grounded", True) and (cur_tick % 24 == 0):
            # Evasive hopping while under fire
            jump = True

        move_len = math.hypot(move_fx, move_fy)
        if move_len > 0.001:
            move_cmd = [move_fx / move_len, move_fy / move_len]
        else:
            move_cmd = [ucx, ucy]

        # -------------------------------------------------------------
        # 6. COMBAT & FIRE DISCIPLINE
        # -------------------------------------------------------------
        # Strict rules:
        # Case 1: Endgame (alive_count <= 3) -> Hunt and eliminate
        # Case 2: Self Defense -> Enemy is close (< 22m) AND (attacking us or < 15m)
        fire = [False, False]
        combat_target: Optional[mf.ActorObs] = None

        if alive_count <= 3:
            # Endgame aggressive mode
            if enemies:
                combat_target = min(enemies, key=lambda m: (m.get("hp", 1400), math.hypot(m["pos"][0] - x, m["pos"][1] - y)))
        elif is_under_attack and attacker_enemy is not None:
            if min_enemy_dist < 28.0:
                combat_target = attacker_enemy
        elif min_enemy_dist < 18.0 and closest_enemy is not None:
            # Extreme proximity self-defense
            combat_target = closest_enemy

        if combat_target is not None:
            tx, ty, tz = combat_target["pos"]
            vx, vy, vz = combat_target.get("vel", [0.0, 0.0, 0.0])
            t_dist = math.hypot(tx - x, ty - y)
            t_flight = t_dist / max(100.0, self.gun_speed)

            lead_x = tx + vx * t_flight
            lead_y = ty + vy * t_flight
            lead_z = tz + 4.5 + vz * t_flight

            diff_x = lead_x - x
            diff_y = lead_y - y
            diff_z = lead_z - (z + 7.0)

            aim_yaw = math.atan2(diff_y, diff_x)
            aim_pitch = math.atan2(diff_z, math.hypot(diff_x, diff_y))
            aim_cmd = [aim_yaw, aim_pitch]
            fire = [True, True]
            mf.draw.line([x, y, z + 7.0], [lead_x, lead_y, lead_z], "#00ff88")
        else:
            # Peace mode: face direction of movement to scan ahead
            face_yaw = math.atan2(move_cmd[1], move_cmd[0])
            aim_cmd = [face_yaw, 0.0]

        # Tactical Reload when silent and not in immediate danger
        primary = me["weapons"][0]
        if primary and primary.get("ammo", 12) <= 4 and combat_target is None:
            act["reload"] = True

        act.update(
            move=move_cmd,
            aim=aim_cmd,
            fire=fire,
            boost=boost,
            jump=jump,
        )

        return act


mf.run(Bot)
