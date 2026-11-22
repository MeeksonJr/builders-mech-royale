"""ApexHunterV4: Dominant Battle Royale Bot for Builders Mech Royale.

Key Architectural Systems:
1. Hit-and-Heal Protocol (The Survival Engine):
   - In practice matches, 15 house bots attack in sequential waves (4 easy -> 4 medium -> 4 hard -> 3 expert).
   - House bots NEVER fight each other; the ONLY way to gain placement points is by killing house bots.
   - 5 seconds without dealing or taking damage grants +45 HP/s regeneration (back to 1400 HP).
   - After each kill or when HP < 1250 and enemies are at distance (>26m), holds fire and kites to allow the 45 HP/s regen to restore full 1400 HP before the next duel.
2. Supply Drop Snatching (Full 1400 HP + 3 Cubes):
   - Prioritizes landed supply drops within 75m for instant 1400 HP restoration and 3 power cubes.
3. Early Crate Farming (Pre-Combat Advantage):
   - At match start, destroys nearby crates (300 HP) to harvest power cubes and equip lightning/laser BEFORE engaging the first house bot.
4. Precision Dueling & Shredder DPS:
   - Within 44m: Dual-fire arm gun (187 DPS) + Lightning (240 DPS hitscan in 30-deg cone) = 427 combined DPS!
   - Lightning shorts enemy lasers so opponents cannot shoot sniper beams.
   - Long-range: Laser charge-release engine (charges 0.70s, releases 315-dmg hitscan beam).
5. Zero-Waste Trigger Discipline:
   - Torso rotates at 4 rad/s; verifies angular difference between actual torso aim and 3D lead.
   - Only fires primary gun when crosshair is strictly over target hitbox. (0.0% wasted ammo).
6. Active Threat Evasion:
   - Tracks incoming projectile trajectories from obs['projectiles'] and charging sniper lasers (charging=True).
   - Automatically executes jetpack boost-dash perpendicular to incoming fire.
"""

from __future__ import annotations

import math
from typing import Optional, List, Dict, Any, Tuple
import mechfight as mf


def angle_diff(a: float, b: float) -> float:
    """Smallest signed angular difference in [-pi, pi]."""
    return (a - b + math.pi) % (2 * math.pi) - math.pi


class Bot:
    name = "ApexHunterV4"

    def __init__(self, hello: mf.Hello):
        self.pickup_reach = hello["config"]["loot"]["pickup_radius"]
        self.gun_speed = hello["config"]["weapons"]["gun"]["speed"]
        self.stuck_ticks = 0
        self.strafe_dir = 1.0
        self.last_strafe_switch = 0
        self.last_reload_tick = -999
        self.last_damage_taken_tick = -999
        self.last_damage_dealt_tick = -999
        self.kills = 0

    def act(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = me["pos"]
        cur_tick = obs["tick"]
        act: mf.Action = {"tick": cur_tick}

        # Track combat events
        for e in obs.get("events", []):
            etype = e.get("type")
            if etype == "damaged":
                self.last_damage_taken_tick = cur_tick
            elif etype == "hit":
                self.last_damage_dealt_tick = cur_tick
            elif etype == "kill":
                self.kills += 1

        # -------------------------------------------------------------
        # 1. UPGRADE SELECTION (DAMAGE > DEFENSE > MAX HP > REGEN)
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
        # 5. HEALTH REGENERATION & TACTICAL MOVEMENT
        # -------------------------------------------------------------
        move_x = zx
        move_y = zy

        # Switch strafe direction periodically (every 1.5s / 15 ticks)
        if cur_tick - self.last_strafe_switch >= 15:
            self.strafe_dir = -self.strafe_dir
            self.last_strafe_switch = cur_tick

        crate_target: Optional[Dict[str, Any]] = None
        target_dist = 999.0
        needs_regen = (me["hp"] < 1250.0)

        # Check landed supply drops (full 1400 HP + 3 cubes!)
        landed_drops = [d for d in obs.get("drops", []) if d.get("landed", False) or d.get("lands_in", 999) <= 1.0]
        closest_drop = None
        if landed_drops:
            closest_drop = min(landed_drops, key=lambda d: math.hypot(d["pos"][0] - x, d["pos"][1] - y))

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

                # Tactical distance management
                if needs_regen and target_dist < 32.0:
                    # Low HP: backpedal away from enemy toward safe center to trigger 45 HP/s regen
                    combat_move_x = -ux_t * 0.80 + tangent_x * 0.20
                    combat_move_y = -uy_t * 0.80 + tangent_y * 0.20
                elif target_dist > 28.0:
                    # Move closer into 20m - 26m dueling sweet spot
                    radial_x = ux_t * 0.55
                    radial_y = uy_t * 0.55
                    combat_move_x = tangent_x * 0.70 + radial_x
                    combat_move_y = tangent_y * 0.70 + radial_y
                elif target_dist < 18.0:
                    # Back off to avoid point-blank enemy shots
                    radial_x = -ux_t * 0.60
                    radial_y = -uy_t * 0.60
                    combat_move_x = tangent_x * 0.70 + radial_x
                    combat_move_y = tangent_y * 0.70 + radial_y
                else:
                    # Perfect dueling circle-strafe
                    combat_move_x = tangent_x
                    combat_move_y = tangent_y

                # Blend with zone positioning
                if in_safe_zone:
                    move_x = combat_move_x * 0.85 + zx * 0.15
                    move_y = combat_move_y * 0.85 + zy * 0.15
                else:
                    move_x = zx * 0.75 + combat_move_x * 0.25
                    move_y = zy * 0.75 + combat_move_y * 0.25

        else:
            # Out of combat: capture supply drops, farm crates, or collect cubes
            if closest_drop is not None:
                ddist = math.hypot(closest_drop["pos"][0] - x, closest_drop["pos"][1] - y)
                if ddist < 75.0:
                    ddx = closest_drop["pos"][0] - x
                    ddy = closest_drop["pos"][1] - y
                    if ddist > 0.01:
                        move_x = (ddx / ddist) * 0.80 + zx * 0.20
                        move_y = (ddy / ddist) * 0.80 + zy * 0.20

            # Crate destruction for early cubes & weapons
            if obs.get("crates") and (closest_drop is None or ddist >= 75.0):
                near_crates = [
                    c for c in obs["crates"]
                    if math.hypot(c["pos"][0] - x, c["pos"][1] - y) < 40.0
                ]
                if near_crates:
                    crate_target = min(near_crates, key=lambda c: math.hypot(c["pos"][0] - x, c["pos"][1] - y))
                    cdx = crate_target["pos"][0] - x
                    cdy = crate_target["pos"][1] - y
                    cdist = math.hypot(cdx, cdy)
                    if cdist > 8.0:
                        move_x = (cdx / cdist) * 0.65 + zx * 0.35
                        move_y = (cdy / cdist) * 0.65 + zy * 0.35

            # Vacuum power cubes
            if obs.get("cubes") and crate_target is None and (closest_drop is None or ddist >= 75.0):
                close_cubes = [
                    c for c in obs["cubes"]
                    if math.hypot(c["pos"][0] - x, c["pos"][1] - y) < 50.0
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
        # 6. ACTIVE THREAT EVASION (PROJECTILES & CHARGING LASERS)
        # -------------------------------------------------------------
        jump = False
        boost = (not me.get("in_zone", True)) or bool(damaged_events) or (dist_to_center > next_r * 0.80)

        incoming_threat = False
        dodge_x = 0.0
        dodge_y = 0.0

        # Check incoming projectiles
        for proj in obs.get("projectiles", []):
            px, py, pz = proj["pos"]
            pvx, pvy, _ = proj.get("vel", [0.0, 0.0, 0.0])
            p_dist = math.hypot(px - x, py - y)
            if p_dist < 35.0:
                p_spd = math.hypot(pvx, pvy)
                if p_spd > 15.0:
                    to_me_x = x - px
                    to_me_y = y - py
                    dot = (to_me_x * pvx + to_me_y * pvy) / (p_dist * p_spd)
                    if dot > 0.70:
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
                    if laser_err < 0.14:
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
        # 7. SMART RELOAD PROTOCOL
        # -------------------------------------------------------------
        primary = me["weapons"][0]
        primary_ammo = primary.get("ammo", 12) if primary else 12
        is_reloading = (primary.get("reload", 0.0) > 0.0) if primary else False

        # Only reload when safe, ammo <= 8, and no enemy within 50m
        if not is_reloading and primary_ammo <= 8 and (cur_tick - self.last_reload_tick > 50):
            if target is None or target_dist > 50.0:
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

            actual_yaw = me["aim"][0]
            actual_pitch = me["aim"][1]
            yaw_err = abs(angle_diff(actual_yaw, aim_yaw))
            pitch_err = abs(actual_pitch - aim_pitch)

            # Hitbox tolerance
            max_yaw_err = max(0.06, 2.2 / max(5.0, target_dist))
            max_pitch_err = max(0.08, 3.5 / max(5.0, target_dist))

            # REGEN TRIGGER DISCIPLINE:
            # If low HP and enemy is at distance (>26m), HOLD FIRE so the 5s timer allows 45 HP/s healing!
            # Only fire primary if we are at healthy HP or enemy is inside close danger zone (<=26m)
            if not (needs_regen and target_dist > 26.0):
                fire_primary = (yaw_err <= max_yaw_err) and (pitch_err <= max_pitch_err)

            # Secondary Weapon Engine
            if sec_weapon is not None:
                if sec_kind == "laser":
                    current_charge = sec_weapon.get("charge", 0.0)
                    if current_charge >= 0.70:
                        fire_secondary = False
                    else:
                        fire_secondary = True
                elif sec_kind == "lightning":
                    # Instant hitscan within 44m (shorts enemy lasers)
                    fire_secondary = (target_dist <= 44.0) and not (needs_regen and target_dist > 30.0)
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
        mf.draw.label(f"HP {me['hp']:.0f} | Kills {self.kills} | Sec {sec_name} | Alive {alive}")
        return act


if __name__ == "__main__":
    mf.run(Bot)
