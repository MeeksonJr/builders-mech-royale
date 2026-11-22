"""ApexHunterV3: Apex Combat, Evasion & Dueling Bot for Builders Mech Royale.

Key Architectural Improvements in V3:
1. True Trigger Discipline & Crosshair Alignment:
   - Torso turns at turn_rate (4 rad/s); shots leave along actual torso aim, NOT commanded aim!
   - Calculates exact angular difference between torso aim and target 3D lead.
   - ONLY fires primary gun when crosshair is verified over target hitbox. Eliminates wasted ammo.
2. Active Missile, Projectile & Laser Dodging:
   - Tracks incoming projectiles from obs['projectiles'] heading towards us.
   - Detects enemy mechs charging lasers (charging=True) aimed along our bearing.
   - Executes immediate jetpack boost-dash perpendicular to incoming fire to dodge bursts.
3. Weapon Triangle & Dueling Engine:
   - Lightning: 45m range, 240 DPS, instant hitscan with 30-deg cone, shorts enemy lasers! Always active in combat.
   - Laser: Charges up to 0.70s, then releases trigger (fire=False) to unleash 315-dmg hitscan beam.
   - Mortar: Medium-range ballistic artillery.
4. Smart Ammo & Safe-Reload Protocol:
   - NEVER manually reloads while an enemy is within 55m and visible. Auto-reload handles empty magazines.
   - Only triggers early reload during combat lulls when ammo <= 8 and no threat is within 55m.
5. Critical Rear-Armor Flanking & Circle-Strafing:
   - Exploits 1.5x rear-armor critical multiplier on vulnerable enemy targets.
   - Circle-strafes at optimal 20m - 28m combat radius with directional switches to disrupt enemy aim.
6. Power Cube Vacuum & Drop Capture:
   - Prioritizes damage > defense > health_regen upgrades.
   - Immediately collects dropped cubes from kills and captures landed supply drops for full 1400 HP + 3 cubes.
"""

from __future__ import annotations

import math
from typing import Optional, List, Dict, Any, Tuple
import mechfight as mf


def angle_diff(a: float, b: float) -> float:
    """Smallest signed angular difference in [-pi, pi]."""
    return (a - b + math.pi) % (2 * math.pi) - math.pi


class Bot:
    name = "ApexHunterV3"

    def __init__(self, hello: mf.Hello):
        self.pickup_reach = hello["config"]["loot"]["pickup_radius"]
        self.gun_speed = hello["config"]["weapons"]["gun"]["speed"]
        self.stuck_ticks = 0
        self.strafe_dir = 1.0
        self.last_strafe_switch = 0
        self.last_reload_tick = -999

    def act(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        cur_tick = obs["tick"]
        act: mf.Action = {"tick": cur_tick}

        # -------------------------------------------------------------
        # 1. UPGRADE SELECTION (DAMAGE > DEFENSE > REGEN > MOBILITY)
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
        # 3. COMBAT TARGET EVALUATION (WEAKEST / REAR-EXPOSED / ATTACKER)
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
            def enemy_priority(em: mf.ActorObs) -> float:
                edist = math.hypot(em["pos"][0] - x, em["pos"][1] - y)
                ehp = em.get("hp", 1400.0)
                # Check if enemy rear is exposed (angle difference > 90 deg from enemy aim)
                em_yaw = em.get("aim", [0.0, 0.0])[0]
                shot_yaw = math.atan2(y - em["pos"][1], x - em["pos"][0])
                angle_diff_val = abs(angle_diff(shot_yaw, em_yaw))
                rear_bonus = 0.65 if angle_diff_val > (math.pi / 2) else 1.0
                return (ehp * 0.4 + edist * 1.5) * rear_bonus

            target = min(enemies, key=enemy_priority)

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

        # Switch strafe direction periodically (every 1.5s / 15 ticks)
        if cur_tick - self.last_strafe_switch >= 15:
            self.strafe_dir = -self.strafe_dir
            self.last_strafe_switch = cur_tick

        crate_target: Optional[Dict[str, Any]] = None
        target_dist = 999.0

        if target is not None:
            tx, ty, _ = target["pos"]
            dx_t = tx - x
            dy_t = ty - y
            target_dist = math.hypot(dx_t, dy_t)

            if target_dist > 0.01:
                ux_t = dx_t / target_dist
                uy_t = dy_t / target_dist

                # Tangent vector for circle strafing
                tangent_x = -uy_t * self.strafe_dir
                tangent_y = ux_t * self.strafe_dir

                # Range control: maintain 20m - 28m sweet spot
                if target_dist > 28.0:
                    radial_x = ux_t * 0.55
                    radial_y = uy_t * 0.55
                elif target_dist < 18.0:
                    radial_x = -ux_t * 0.60
                    radial_y = -uy_t * 0.60
                else:
                    radial_x = 0.0
                    radial_y = 0.0

                combat_move_x = tangent_x * 0.70 + radial_x
                combat_move_y = tangent_y * 0.70 + radial_y

                # If low HP (< 400), pull back to trigger 45 HP/s regen
                if me["hp"] < 400.0 and target_dist < 30.0:
                    combat_move_x = -ux_t * 0.85 + tangent_x * 0.15
                    combat_move_y = -uy_t * 0.85 + tangent_y * 0.15

                if in_safe_zone:
                    move_x = combat_move_x * 0.85 + zx * 0.15
                    move_y = combat_move_y * 0.85 + zy * 0.15
                else:
                    move_x = zx * 0.75 + combat_move_x * 0.25
                    move_y = zy * 0.75 + combat_move_y * 0.25
        else:
            # Out of combat: capture landed supply drops or farm crates/cubes
            landed_drops = [d for d in obs.get("drops", []) if d.get("landed", False)]
            if landed_drops:
                closest_drop = min(landed_drops, key=lambda d: math.hypot(d["pos"][0] - x, d["pos"][1] - y))
                ddist = math.hypot(closest_drop["pos"][0] - x, closest_drop["pos"][1] - y)
                if ddist < 60.0:
                    ddx = closest_drop["pos"][0] - x
                    ddy = closest_drop["pos"][1] - y
                    if ddist > 0.01:
                        move_x = (ddx / ddist) * 0.80 + zx * 0.20
                        move_y = (ddy / ddist) * 0.80 + zy * 0.20

            # Crate destruction for cubes and weapons
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
                        move_x = (cdx / cdist) * 0.65 + zx * 0.35
                        move_y = (cdy / cdist) * 0.65 + zy * 0.35

            # Vacuum nearby power cubes
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
        # 6. ACTIVE THREAT EVASION (PROJECTILES & LASER SNIPERS)
        # -------------------------------------------------------------
        jump = False
        boost = (not me.get("in_zone", True)) or bool(damaged_events) or (dist_to_center > next_r * 0.80)

        incoming_threat = False
        dodge_x = 0.0
        dodge_y = 0.0

        # Check incoming projectiles (mortar shells / rocket bursts)
        for proj in obs.get("projectiles", []):
            px, py, pz = proj["pos"]
            pvx, pvy, _ = proj.get("vel", [0.0, 0.0, 0.0])
            p_dist = math.hypot(px - x, py - y)
            if p_dist < 35.0:
                p_spd = math.hypot(pvx, pvy)
                if p_spd > 15.0:
                    # Dot product to check if flying toward me
                    to_me_x = x - px
                    to_me_y = y - py
                    dot = (to_me_x * pvx + to_me_y * pvy) / (p_dist * p_spd)
                    if dot > 0.70:  # Direct inbound projectile!
                        incoming_threat = True
                        dodge_x = -pvy / p_spd
                        dodge_y = pvx / p_spd
                        break

        # Check enemy laser snipers charging along our line of sight
        if not incoming_threat:
            for em in enemies:
                if em.get("charging"):
                    em_yaw = em.get("aim", [0.0, 0.0])[0]
                    em_to_me_yaw = math.atan2(y - em["pos"][1], x - em["pos"][0])
                    laser_err = abs(angle_diff(em_yaw, em_to_me_yaw))
                    if laser_err < 0.14:  # Sniper aim aligned with us!
                        incoming_threat = True
                        dodge_x = -math.sin(em_yaw)
                        dodge_y = math.cos(em_yaw)
                        break

        if incoming_threat:
            jump = True
            boost = True
            move_x = dodge_x
            move_y = dodge_y

        # Anti-stuck escape
        actual_spd = math.hypot(me["vel"][0], me["vel"][1])
        want_move = math.hypot(move_x, move_y) > 0.20
        if want_move and actual_spd < 1.1 and me.get("grounded", True):
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0

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
        # 7. SMART RELOAD PROTOCOL (SAFE RELOADS ONLY)
        # -------------------------------------------------------------
        primary = me["weapons"][0]
        primary_ammo = primary.get("ammo", 12) if primary else 12
        is_reloading = (primary.get("reload", 0.0) > 0.0) if primary else False

        # Never reload manually during active duel! Empty mag auto-reloads.
        # Only reload when out of combat, ammo <= 8, and no enemy within 55m.
        if not is_reloading and primary_ammo <= 8 and (cur_tick - self.last_reload_tick > 50):
            if target is None or target_dist > 55.0:
                act["reload"] = True
                self.last_reload_tick = cur_tick

        # -------------------------------------------------------------
        # 8. TRIGGER DISCIPLINE & PRECISION WEAPON ENGINE
        # -------------------------------------------------------------
        fire_primary = False
        fire_secondary = False
        sec_weapon = me["weapons"][1]
        sec_kind = sec_weapon.get("kind", "") if sec_weapon else ""

        if target is not None:
            tx, ty, tz = target["pos"]
            vx, vy, vz = target.get("vel", [0.0, 0.0, 0.0])

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

            # Trigger discipline: only pull trigger when torso actual aim is aligned!
            actual_yaw = me["aim"][0]
            actual_pitch = me["aim"][1]
            yaw_err = abs(angle_diff(actual_yaw, aim_yaw))
            pitch_err = abs(actual_pitch - aim_pitch)

            # Hitbox tolerance (mech is ~4.5m wide, 9m tall)
            max_yaw_err = max(0.06, 2.2 / max(5.0, target_dist))
            max_pitch_err = max(0.08, 3.5 / max(5.0, target_dist))
            fire_primary = (yaw_err <= max_yaw_err) and (pitch_err <= max_pitch_err)

            # Secondary Weapon Engine
            if sec_weapon is not None:
                if sec_kind == "laser":
                    current_charge = sec_weapon.get("charge", 0.0)
                    if current_charge >= 0.70:
                        # Release trigger to fire 315-dmg hitscan beam when aligned
                        fire_secondary = False
                    else:
                        # Hold trigger to build charge
                        fire_secondary = True
                elif sec_kind == "lightning":
                    # Instant hitscan within 45m; always fire in combat to shred DPS and short lasers
                    fire_secondary = (target_dist <= 44.0)
                elif sec_kind == "mortar":
                    fire_secondary = (15.0 <= target_dist <= 65.0) and (yaw_err <= 0.15)
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
            # Shoot crate for power cubes & weapons
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
            # Scan forward along movement vector
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


if __name__ == "__main__":
    mf.run(Bot)
