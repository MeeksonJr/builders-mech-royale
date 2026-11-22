"""ApexHunter: Dominant Combat, Dueling & Survival Bot for Builders Mech Royale.

Strategy:
1. Divide and Conquer:
   - In practice mode, the 15 house bots act as a squad and send mechs weakest-first (one early, more as you thin them out).
   - House bots never kill each other! The ONLY way to climb the leaderboard is to actively HUNT and ELIMINATE the easy/medium bots in sequential 1-on-1 duels!
   - Every kill directly increases placement: 4 easy + 2 medium = ~6.5 placement score!
2. High-Precision Ballistic Leading:
   - Uses exact projectile flight physics (t = dist / 252 m/s) with 3D velocity compensation to land arm gun shots reliably.
   - Dual-fire (Slot 0 Gun + Slot 1 Special) active during combat for maximum burst DPS.
3. Combat Circle-Strafing & Frontal Armor:
   - Always faces the nearest attacker to protect rear armor (1.5x damage multiplier).
   - Strafe tangentially to enemy line-of-fire with periodic directional shifts to dodge enemy projectiles.
   - Evasive vertical hops to throw off enemy horizontal crosshairs.
4. Relentless Power Cube & Loot Harvesting:
   - Immediately collects cubes dropped from kills and nearby crates to level up.
   - Upgrade priority: Damage/DPS > Armor/Defense > Health/Regen > Mobility.
5. Strict Safe-Zone Positioning:
   - Ensures combat happens comfortably inside the safe circle, preventing storm pinches.
"""

from __future__ import annotations

import math
from typing import Optional, List, Dict, Any
import mechfight as mf


class Bot:
    name = "ApexHunterV1"

    def __init__(self, hello: mf.Hello):
        self.pickup_reach = hello["config"]["loot"]["pickup_radius"]
        self.gun_speed = hello["config"]["weapons"]["gun"]["speed"]
        self.stuck_ticks = 0
        self.strafe_dir = 1.0
        self.last_strafe_switch = 0

    def act(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        cur_tick = obs["tick"]
        act: mf.Action = {"tick": cur_tick}

        # -------------------------------------------------------------
        # 1. UPGRADE SELECTION (DPS & SURVIVABILITY)
        # -------------------------------------------------------------
        if obs.get("offer"):
            choices = obs["offer"]["choices"]
            best_idx = 0
            best_score = -999.0
            for idx, c in enumerate(choices):
                k = c.get("kind", "").lower()
                desc = c.get("desc", "").lower()
                text = f"{k} {desc}"
                score = 50.0
                if any(w in text for w in ("damage", "dps", "power", "cooldown", "fire rate")):
                    score = 160.0
                elif any(w in text for w in ("armor", "plating", "reduction", "shield")):
                    score = 150.0
                elif any(w in text for w in ("health", "max hp", "hp", "regen")):
                    score = 140.0
                elif any(w in text for w in ("lightning", "laser", "mortar")):
                    score = 120.0
                elif any(w in text for w in ("speed", "jet", "energy", "boost")):
                    score = 100.0
                if score > best_score:
                    best_score = score
                    best_idx = idx
            act["pick"] = best_idx

        # -------------------------------------------------------------
        # 2. WEAPON LOOTING
        # -------------------------------------------------------------
        if me["weapons"][1] is None and obs.get("pickups"):
            near_picks = [
                p for p in obs["pickups"]
                if math.hypot(p["pos"][0] - x, p["pos"][1] - y) < self.pickup_reach
            ]
            if near_picks:
                pref = {"lightning": 5, "laser": 4, "mortar": 3, "gun": 1}
                chosen = max(near_picks, key=lambda p: pref.get(p.get("kind", ""), 0))
                act["take"] = chosen["id"]

        # -------------------------------------------------------------
        # 3. COMBAT TARGET SELECTION (FOCUS WEAKEST / ACTIVE ATTACKER)
        # -------------------------------------------------------------
        enemies: List[mf.ActorObs] = [m for m in obs.get("mechs", []) if m["kind"] == "mech"]
        damaged_events = [e for e in obs.get("events", []) if e.get("type") == "damaged" and e.get("by") is not None]

        target: Optional[mf.ActorObs] = None

        # Priority 1: Direct attacker if currently taking damage
        if damaged_events:
            attacker_id = damaged_events[-1]["by"]
            for em in enemies:
                if em["id"] == attacker_id:
                    target = em
                    break

        # Priority 2: Closest visible enemy, weighted heavily toward lower HP (easier kill)
        if target is None and enemies:
            def enemy_killability(em: mf.ActorObs) -> float:
                edist = math.hypot(em["pos"][0] - x, em["pos"][1] - y)
                ehp = em.get("hp", 1400.0)
                # Lower score = better target
                return ehp * 0.5 + edist * 2.0

            target = min(enemies, key=enemy_killability)

        # -------------------------------------------------------------
        # 4. ZONE SAFETY & POSITIONING VECTORS
        # -------------------------------------------------------------
        zone = obs["zone"]
        next_c = zone.get("next_center", zone.get("center", [0.0, 0.0]))
        next_r = zone.get("next_radius", zone.get("radius", 200.0))

        dx_zone = next_c[0] - x
        dy_zone = next_c[1] - y
        dist_to_center = math.hypot(dx_zone, dy_zone)

        if dist_to_center > 0.01:
            zx = dx_zone / dist_to_center
            zy = dy_zone / dist_to_center
        else:
            zx, zy = 0.0, 0.0

        in_safe_zone = me.get("in_zone", True) and (dist_to_center < next_r * 0.85)

        # -------------------------------------------------------------
        # 5. TACTICAL MOVEMENT: CIRCLE-STRAFE & RANGE CONTROL
        # -------------------------------------------------------------
        move_x = zx
        move_y = zy

        # Switch strafe direction periodically (every 1.5 seconds / 15 ticks)
        if cur_tick - self.last_strafe_switch >= 15:
            self.strafe_dir = -self.strafe_dir
            self.last_strafe_switch = cur_tick

        if target is not None:
            tx, ty, _ = target["pos"]
            dx_t = tx - x
            dy_t = ty - y
            d_t = math.hypot(dx_t, dy_t)

            if d_t > 0.01:
                ux_t = dx_t / d_t
                uy_t = dy_t / d_t

                # Tangent vector for circle strafing
                tangent_x = -uy_t * self.strafe_dir
                tangent_y = ux_t * self.strafe_dir

                # Maintain optimal combat range (18m - 28m)
                # If too far (>28m), close in. If too close (<16m), back up slightly.
                if d_t > 28.0:
                    radial_x = ux_t * 0.50
                    radial_y = uy_t * 0.50
                elif d_t < 16.0:
                    radial_x = -ux_t * 0.50
                    radial_y = -uy_t * 0.50
                else:
                    radial_x = 0.0
                    radial_y = 0.0

                combat_move_x = tangent_x * 0.70 + radial_x
                combat_move_y = tangent_y * 0.70 + radial_y

                if in_safe_zone:
                    # Comfortably inside zone: prioritize full combat circle-strafe
                    move_x = combat_move_x * 0.75 + zx * 0.25
                    move_y = combat_move_y * 0.75 + zy * 0.25
                else:
                    # Near storm edge or outside: pull strongly toward zone center
                    move_x = zx * 0.70 + combat_move_x * 0.30
                    move_y = zy * 0.70 + combat_move_y * 0.30
        else:
            # No enemies visible: move into safe zone and vacuum nearby power cubes
            if obs.get("cubes"):
                close_cubes = [
                    c for c in obs["cubes"]
                    if math.hypot(c["pos"][0] - x, c["pos"][1] - y) < 40.0
                ]
                if close_cubes:
                    best_c = min(close_cubes, key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                    cdx = best_c["pos"][0] - x
                    cdy = best_c["pos"][1] - y
                    cmag = math.hypot(cdx, cdy)
                    if cmag > 0.01:
                        cube_ux = cdx / cmag
                        cube_uy = cdy / cmag
                        if in_safe_zone:
                            move_x = cube_ux * 0.70 + zx * 0.30
                            move_y = cube_uy * 0.70 + zy * 0.30
                        else:
                            move_x = zx * 0.70 + cube_ux * 0.30
                            move_y = zy * 0.70 + cube_uy * 0.30

        # -------------------------------------------------------------
        # 6. ANTI-STUCK & EVASIVE HOPPING
        # -------------------------------------------------------------
        actual_spd = math.hypot(me["vel"][0], me["vel"][1])
        want_move = math.hypot(move_x, move_y) > 0.25

        if want_move and actual_spd < 1.2 and me.get("grounded", True):
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0

        jump = False
        boost = (not me.get("in_zone", True)) or bool(damaged_events) or (dist_to_center > next_r * 0.80)

        # Stuck escape: jetpack jump and slide 90 degrees tangent
        if self.stuck_ticks >= 2:
            jump = True
            boost = True
            move_x, move_y = -move_y, move_x

        # Combat hop: small evasive hops to dodge enemy projectiles
        if target is not None and me.get("grounded", True) and (cur_tick % 18 == 0):
            jump = True

        move_len = math.hypot(move_x, move_y)
        if move_len > 0.001:
            move_cmd = [move_x / move_len, move_y / move_len]
        else:
            move_cmd = [zx, zy]

        # -------------------------------------------------------------
        # 7. PREDICTIVE BALLISTIC AIMING & DUAL FIRE
        # -------------------------------------------------------------
        if target is not None:
            tx, ty, tz = target["pos"]
            vx, vy, vz = target.get("vel", [0.0, 0.0, 0.0])
            target_dist = math.hypot(tx - x, ty - y)

            # Bullet flight time with lead calculation
            t_flight = target_dist / max(100.0, self.gun_speed)
            lead_x = tx + vx * t_flight
            lead_y = ty + vy * t_flight
            lead_z = tz + 4.5 + vz * t_flight

            diff_x = lead_x - x
            diff_y = lead_y - y
            diff_z = lead_z - (z + 7.0)
            horiz_dist = math.hypot(diff_x, diff_y)

            aim_yaw = math.atan2(diff_y, diff_x)
            aim_pitch = math.atan2(diff_z, max(0.1, horiz_dist))

            # Fire both primary gun and secondary weapon
            act.update(
                move=move_cmd,
                aim=[aim_yaw, aim_pitch],
                fire=[True, True],
                boost=boost,
                jump=jump,
            )
            mf.draw.line([x, y, z + 7.0], [lead_x, lead_y, lead_z], "#ff2200")
            mf.draw.point([lead_x, lead_y, lead_z], "#ffff00", 5)
        else:
            # No target: aim in direction of movement to scan ahead
            face_yaw = math.atan2(move_cmd[1], move_cmd[0]) if (abs(move_cmd[0]) > 0.01 or abs(move_cmd[1]) > 0.01) else 0.0
            act.update(
                move=move_cmd,
                aim=[face_yaw, 0.0],
                fire=[False, False],
                boost=boost,
                jump=jump,
            )

        # Debug HUD
        alive = obs.get("alive", 16)
        mf.draw.point([next_c[0], next_c[1], z], "#00ff88", 6)
        mf.draw.label(f"HP {me['hp']:.0f} | Alive {alive} | Target: {'LOCKED' if target else 'SCANNING'}")
        return act


mf.run(Bot)
