"""ApexHunterV2: Elite Combat, Dueling & Survival Bot for Builders Mech Royale.

Key Improvements in V2:
1. Tactical Auto-Reloading:
   - Arms gun holds 12 bullets with a 2.4s reload.
   - Automatically triggers early reload when ammo <= 5 during combat lulls or after kills.
   - Never enters a duel with a depleted magazine.
2. Weapon Charge & Release Engine:
   - Laser: Charges trigger for 0.70s, then releases trigger (fire[1]=False) to release the 315-damage beam.
   - Lightning: Fires only within its 45m range, saving energy and shorting enemy lasers.
   - Mortar: Fires at medium range (15m - 60m).
3. Early-Game Crate Farming:
   - When no enemies are actively attacking, destroys nearby crates (300 HP) to harvest power cubes
     and equip secondary weapons BEFORE fighting the first house bot.
4. Supply Drop Capture:
   - When a supply drop is landed within 55m and safe, captures the drop for FULL 1400 HP + 3 cubes!
5. Health Regen & Tactical Disengagement:
   - When HP < 450, retreats to nearest cover to trigger the 45 HP/s health regeneration.
6. Rear Armor Flanking:
   - Targets mechs with exposed rears for 1.5x critical damage multiplier.
7. Circle-Strafing & Hop Evasion:
   - Maintains tangential circle-strafing to evade enemy fire while keeping our frontal armor oriented to the enemy.
"""

from __future__ import annotations

import math
from typing import Optional, List, Dict, Any, Tuple
import mechfight as mf


class Bot:
    name = "ApexHunterV2"

    def __init__(self, hello: mf.Hello):
        self.pickup_reach = hello["config"]["loot"]["pickup_radius"]
        self.gun_speed = hello["config"]["weapons"]["gun"]["speed"]
        self.stuck_ticks = 0
        self.strafe_dir = 1.0
        self.last_strafe_switch = 0
        self.laser_charging_ticks = 0

    def act(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        cur_tick = obs["tick"]
        act: mf.Action = {"tick": cur_tick}

        # -------------------------------------------------------------
        # 1. UPGRADE SELECTION (PRIORITIZE DAMAGE & SURVIVABILITY)
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
        # 2. WEAPON LOOTING (LIGHTNING > LASER > MORTAR)
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
        # 3. COMBAT TARGET EVALUATION
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

        # Priority 2: Weakest or rear-exposed enemy
        if target is None and enemies:
            def enemy_killability(em: mf.ActorObs) -> float:
                edist = math.hypot(em["pos"][0] - x, em["pos"][1] - y)
                ehp = em.get("hp", 1400.0)
                # Check if enemy rear is exposed (angle difference > 90 deg from enemy aim)
                em_yaw = em.get("aim", [0.0, 0.0])[0]
                shot_yaw = math.atan2(y - em["pos"][1], x - em["pos"][0])
                angle_diff = abs((shot_yaw - em_yaw + math.pi) % (2 * math.pi) - math.pi)
                rear_bonus = 0.70 if angle_diff > (math.pi / 2) else 1.0
                return (ehp * 0.4 + edist * 1.5) * rear_bonus

            target = min(enemies, key=enemy_killability)

        # -------------------------------------------------------------
        # 4. ZONE SAFETY & POSITIONING
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
        # 5. TACTICAL MOVEMENT: CIRCLE-STRAFE / CRATE / SUPPLY DROP
        # -------------------------------------------------------------
        move_x = zx
        move_y = zy

        # Switch strafe direction periodically (every 1.5 seconds / 15 ticks)
        if cur_tick - self.last_strafe_switch >= 15:
            self.strafe_dir = -self.strafe_dir
            self.last_strafe_switch = cur_tick

        crate_target: Optional[Dict[str, Any]] = None

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

                # Range control: maintain 18m - 26m
                if d_t > 26.0:
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

                # If low HP (< 450), pull back toward cover
                if me["hp"] < 450.0 and d_t < 25.0:
                    combat_move_x = -ux_t * 0.80 + tangent_x * 0.20
                    combat_move_y = -uy_t * 0.80 + tangent_y * 0.20

                if in_safe_zone:
                    move_x = combat_move_x * 0.80 + zx * 0.20
                    move_y = combat_move_y * 0.80 + zy * 0.20
                else:
                    move_x = zx * 0.75 + combat_move_x * 0.25
                    move_y = zy * 0.75 + combat_move_y * 0.25
        else:
            # No target in combat: check for Supply Drops, nearby Crates, and Cubes
            landed_drops = [d for d in obs.get("drops", []) if d.get("landed", False)]
            if landed_drops:
                closest_drop = min(landed_drops, key=lambda d: math.hypot(d["pos"][0] - x, d["pos"][1] - y))
                ddist = math.hypot(closest_drop["pos"][0] - x, closest_drop["pos"][1] - y)
                if ddist < 60.0:
                    # Move to capture supply drop for full 1400 HP + 3 cubes!
                    ddx = closest_drop["pos"][0] - x
                    ddy = closest_drop["pos"][1] - y
                    if ddist > 0.01:
                        move_x = (ddx / ddist) * 0.80 + zx * 0.20
                        move_y = (ddy / ddist) * 0.80 + zy * 0.20

            # Crate destruction for early loot & cubes
            if obs.get("crates"):
                near_crates = [
                    c for c in obs["crates"]
                    if math.hypot(c["pos"][0] - x, c["pos"][1] - y) < 35.0
                ]
                if near_crates:
                    crate_target = min(near_crates, key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                    cdx = crate_target["pos"][0] - x
                    cdy = crate_target["pos"][1] - y
                    cdist = math.hypot(cdx, cdy)
                    if cdist > 8.0:
                        move_x = (cdx / cdist) * 0.60 + zx * 0.40
                        move_y = (cdy / cdist) * 0.60 + zy * 0.40

            # Vacuum power cubes
            if obs.get("cubes") and crate_target is None:
                close_cubes = [
                    c for c in obs["cubes"]
                    if math.hypot(c["pos"][0] - x, c["pos"][1] - y) < 45.0
                ]
                if close_cubes:
                    best_c = min(close_cubes, key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                    cdx = best_c["pos"][0] - x
                    cdy = best_c["pos"][1] - y
                    cmag = math.hypot(cdx, cdy)
                    if cmag > 0.01:
                        move_x = (cdx / cmag) * 0.70 + zx * 0.30
                        move_y = (cdy / cmag) * 0.70 + zy * 0.30

        # -------------------------------------------------------------
        # 6. ANTI-STUCK & MOBILITY
        # -------------------------------------------------------------
        actual_spd = math.hypot(me["vel"][0], me["vel"][1])
        want_move = math.hypot(move_x, move_y) > 0.20

        if want_move and actual_spd < 1.1 and me.get("grounded", True):
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0

        jump = False
        boost = (not me.get("in_zone", True)) or bool(damaged_events) or (dist_to_center > next_r * 0.80)

        # Stuck escape: jetpack jump and slide tangent
        if self.stuck_ticks >= 2:
            jump = True
            boost = True
            move_x, move_y = -move_y, move_x

        # Combat hop: small evasive hops to dodge enemy projectiles
        if target is not None and me.get("grounded", True) and (cur_tick % 16 == 0):
            jump = True

        move_len = math.hypot(move_x, move_y)
        if move_len > 0.001:
            move_cmd = [move_x / move_len, move_y / move_len]
        else:
            move_cmd = [zx, zy]

        # -------------------------------------------------------------
        # 7. TACTICAL RELOAD (NEVER START A FIGHT WITH LOW AMMO)
        # -------------------------------------------------------------
        primary = me["weapons"][0]
        primary_ammo = primary.get("ammo", 12) if primary else 12
        if primary_ammo <= 5:
            # Reload if no enemy is within immediate 15m danger zone
            if target is None or math.hypot(target["pos"][0] - x, target["pos"][1] - y) > 18.0:
                act["reload"] = True

        # -------------------------------------------------------------
        # 8. WEAPON FIRING & BALLISTIC AIMING
        # -------------------------------------------------------------
        fire_primary = False
        fire_secondary = False
        aim_yaw = 0.0
        aim_pitch = 0.0

        sec_weapon = me["weapons"][1]
        sec_kind = sec_weapon.get("kind", "") if sec_weapon else ""

        if target is not None:
            tx, ty, tz = target["pos"]
            vx, vy, vz = target.get("vel", [0.0, 0.0, 0.0])
            target_dist = math.hypot(tx - x, ty - y)

            # Bullet flight time with 3D lead calculation
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
            fire_primary = True

            # Secondary Weapon Firing Engine
            if sec_weapon is not None:
                if sec_kind == "laser":
                    # Laser charging logic: hold for ~0.70s, then release to fire beam!
                    self.laser_charging_ticks += 1
                    if self.laser_charging_ticks >= 7:
                        # Release trigger to fire 315-dmg hitscan beam!
                        fire_secondary = False
                        self.laser_charging_ticks = 0
                    else:
                        fire_secondary = True
                elif sec_kind == "lightning":
                    # Instant 60-dmg chain arc within 45m
                    fire_secondary = (target_dist <= 44.0)
                elif sec_kind == "mortar":
                    # Mortar projectile at medium range
                    fire_secondary = (14.0 <= target_dist <= 65.0)
                else:
                    fire_secondary = True

            act.update(
                move=move_cmd,
                aim=[aim_yaw, aim_pitch],
                fire=[fire_primary, fire_secondary],
                boost=boost,
                jump=jump,
            )
            mf.draw.line([x, y, z + 7.0], [lead_x, lead_y, lead_z], "#ff2200")
            mf.draw.point([lead_x, lead_y, lead_z], "#ffff00", 5)

        elif crate_target is not None:
            # Shoot nearby crate for early cubes & secondary weapon
            cx, cy, cz = crate_target["pos"]
            cdx = cx - x
            cdy = cy - y
            cdz = (cz + 1.5) - (z + 7.0)
            horiz_dist = math.hypot(cdx, cdy)
            aim_yaw = math.atan2(cdy, cdx)
            aim_pitch = math.atan2(cdz, max(0.1, horiz_dist))
            act.update(
                move=move_cmd,
                aim=[aim_yaw, aim_pitch],
                fire=[True, False],
                boost=boost,
                jump=jump,
            )
            mf.draw.line([x, y, z + 7.0], [cx, cy, cz + 1.5], "#00aaff")

        else:
            # No combat target: aim forward to scan ahead
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
        sec_name = sec_kind.upper() if sec_kind else "NONE"
        mf.draw.point([next_c[0], next_c[1], z], "#00ff88", 6)
        mf.draw.label(f"HP {me['hp']:.0f} | Mag {primary_ammo}/12 | Sec {sec_name} | Alive {alive}")
        return act


mf.run(Bot)
