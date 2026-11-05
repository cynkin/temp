# Reference analysis → construction requirements

20 reference images live in `pics/` (index → file mapping: `references/index.txt`).
Each image was studied visually (12 aspects below), then measured with `envgen.vision.profile`.
The measured profiles (`references/profiles/NN.json`) and their aggregates
(`references/targets/*.json`) are the **numeric QA targets** that the refinement loop optimises against.
The qualitative analysis became the spec in `specs/hospital_corridor.yaml`, plus the props, materials and story beats in code.

> Studio watermarks visible in some references (Leartes, Meshingun, Scans Factory, Sierra Division,
> Unreal Engine) are ignored. The references set the *quality bar and art direction*. No assets,
> layouts or artwork are copied: all geometry, materials and signage are generated from scratch.

## Measured profile per image

`lum` is mean perceptual luma (0–1). `warmth` is the luma-weighted mean of R−B. `haze` is the mean dark channel.
`VP` is the detected vanishing point, normalised to the frame. Detection is unreliable on 12 and 13, and no VP was found for 01, 02 and 14.

| # | lum mean | key | rms contrast | warmth | sat | haze | edge dens | VP (x,y) | palette |
|---|---|---|---|---|---|---|---|---|---|
| 00 | 0.306 | mid | 0.176 | -0.017 | 0.09 | 0.197 | 0.043 | 0.50,0.48 | #2e312c #464d47 #191918 #616b65 |
| 01 | 0.221 | low | 0.237 | +0.079 | 0.20 | 0.092 | 0.050 | - | #0e1113 #292b29 #57564c #eae5cd |
| 02 | 0.162 | low | 0.120 | -0.030 | 0.23 | 0.086 | 0.025 | - | #141010 #252b2d #3c4b4c #3f1d1d |
| 03 | 0.295 | low | 0.252 | +0.071 | 0.16 | 0.111 | 0.082 | 0.24,0.65 | #0f1011 #2a2c2a #4c4d47 #727167 |
| 04 | 0.271 | low | 0.217 | +0.071 | 0.20 | 0.116 | 0.079 | 0.77,0.31 | #110c09 #2d251e #6d6558 #494137 |
| 05 | 0.162 | low | 0.146 | +0.038 | 0.23 | 0.055 | 0.058 | 0.69,0.45 | #0a060f #191623 #302b33 #4b4244 |
| 06 | 0.314 | mid | 0.266 | +0.014 | 0.17 | 0.112 | 0.078 | 0.50,0.50 | #0b0d0b #708272 #374339 #b1c0b2 |
| 07 | 0.221 | low | 0.137 | +0.123 | 0.38 | 0.094 | 0.039 | 0.25,0.43 | #473f2d #120f0b #2e291b #61553e |
| 08 | 0.151 | low | 0.159 | +0.024 | 0.20 | 0.058 | 0.043 | 0.70,0.41 | #090d0b #1d201c #3d3932 #6f665e |
| 09 | 0.316 | mid | 0.200 | +0.048 | 0.14 | 0.172 | 0.070 | 0.41,0.44 | #3a352f #544e46 #1c1916 #7a7369 |
| 10 | 0.249 | low | 0.190 | +0.166 | 0.40 | 0.067 | 0.077 | 0.79,0.64 | #2d271a #4e432d #0f0d08 #86734f |
| 11 | 0.202 | low | 0.210 | +0.141 | 0.34 | 0.054 | 0.063 | 0.64,0.49 | #0c0e10 #2a251a #524022 #76705e |
| 12 | 0.132 | low | 0.130 | +0.139 | 0.53 | 0.033 | 0.037 | 0.24,0.65 | #020d0f #3b2d21 #17201c #6a492e |
| 13 | 0.134 | low | 0.092 | +0.036 | 0.23 | 0.061 | 0.020 | 0.83,0.38 | #14140f #262720 #40453a #36130f |
| 14 | 0.169 | low | 0.170 | +0.045 | 0.22 | 0.058 | 0.063 | - | #0b0813 #242129 #40393c #615551 |
| 15 | 0.222 | low | 0.149 | +0.069 | 0.29 | 0.051 | 0.078 | 0.48,0.52 | #2b291b #19170b #403e2e #5b5948 |
| 16 | 0.242 | low | 0.112 | +0.089 | 0.34 | 0.099 | 0.040 | 0.64,0.32 | #493e2a #515140 #2b281a #10130c |
| 17 | 0.233 | low | 0.158 | +0.051 | 0.17 | 0.126 | 0.048 | 0.48,0.38 | #443d36 #2f2a25 #171513 #615a50 |
| 18 | 0.090 | low | 0.096 | +0.101 | 0.47 | 0.028 | 0.012 | 0.50,0.46 | #080502 #352a20 #20180f #544331 |
| 19 | 0.058 | low | 0.100 | +0.110 | 0.52 | 0.007 | 0.012 | 0.51,0.49 | #0f090a #230508 #3d0408 #76040b |

**Takeaways from the numbers.** Every image is low-key (luminance 0.06–0.32, mean 0.21). Contrast is moderate rather than crushed (RMS about 0.15). The grade is slightly warm (R−B about +0.06) and fairly desaturated, apart from the coloured emergency-light shots (12, 18, 19). Corridor shots put the VP within ±0.1 of frame centre. Detail density is moderate (edge density about 0.045): these scenes are cluttered but *readable*, not noisy.

## Per-image analysis

Fields: **Cat** = category/style · **Comp** = composition · **P/S/T** = primary/secondary/tertiary elements · **Geo** = geometry · **Mat** = materials · **Light** = direction / intensity / CCT · **Shad** = shadows · **Atm** = atmosphere · **Col** = palette & contrast · **Cam** = camera · **Story** = environmental storytelling · **Detail** = small realism cues.

**00 — Painted-brick service tunnel.** Cat: underground utility corridor, institutional. Comp: strict one-point perspective, VP centred; the walls frame the shot and a distant stair landing is the focal point. P/S/T: tunnel walls / caged bulkhead lamp / rubble at the wall base. Geo: about 2.2 m wide barrel of whitewashed brick, flat concrete slab ceiling, recessed pilaster every few metres, stairs at the far end. Mat: limewashed brick with heavy flaking that exposes grey mortar; the concrete floor has cracked plates and a raised broken slab. Light: one caged incandescent bulb overhead (about 5000 K, cold), a second dim lamp far away; falloff is steep. Shad: soft and pooled, the floor beyond the lamp falls dark. Atm: light fog that softens the far end. Col: near-monochrome grey-green, mid key. Cam: about 1.6 m height, about 20 mm, centred. Story: neglect, slow decay, nobody maintains it. Detail: dust at the floor/wall junction, chips scattered near the wall base, uneven brick courses.

**01 — Industrial yard vignette (exterior).** Cat: abandoned plant, prop-hero shot. Comp: low camera (about 30 cm) with a puddle reflection filling the lower third; props form a horizontal band. P/S/T: concrete Jersey barriers, rusted drums / gas cylinders, cone / tyres / dry grass. Geo: chamfered barriers with lifting slots, ribbed drums, tyre stack. Mat: chalky concrete with chipped edges; drums with paint flaking to orange rust; mirror-like puddle. Light: low warm sun from the left (about 4000 K), strong. Shad: crisp and long. Atm: drifting smoke/steam behind the props. Col: dark teal background against warm props, high contrast. Cam: 50–85 mm, shallow depth of field. Story: an abandoned checkpoint or barricade. Detail: gravel, oil sheen, a cone with scuffed reflective bands.

**02 — Hospital corridor under emergency light.** Cat: modern hospital, horror. Comp: foreground gurney on the left frames the shot; the corridor recedes to a red EXIT sign. P/S/T: gurney with a draped sheet / wheelchair and IV pole / debris, cables, wet floor. Mat: wet vinyl floor (very low roughness, glossy streaks), chipped paint, sheet cloth. Light: red emergency sign (key, far), cool cyan fill from a side room. Shad: soft and murky. Atm: dense haze glowing red at the far end. Col: teal and red complementary scheme, low key. Cam: about 1.2 m, 24 mm, off-centre left. Story: a hurried evacuation; something lies under the sheet. Detail: red reflections smeared across the wet floor.

**03 — Industrial complex exterior (wide).** Cat: decaying refinery/power plant. Comp: a wall on the left leads into silos and stacks on the right; heavy sky. P/S/T: plant structures / graffiti wall, barrels / pallets, hydrant, birds. Geo: cylindrical silos, ladders, gantries, corrugated cladding. Mat: rust-streaked corrugated steel, concrete with graffiti. Light: overcast and diffuse with a soft key from the upper right. Atm: hazy depth. Col: desaturated, rust accents. Cam: about 1.7 m, 24 mm. Story: years of abandonment, vandalism. Detail: weeds in cracks, crows.

**04 — Abandoned interior with daylight shafts (scan-based).** Cat: Soviet-era public building. Comp: framed by a pillar and a leaning panel; the window wall is the focal light source. P/S/T: brick pillars, grilles / sofa, corrugated sheet / chains, papers, rubble. Mat: exposed brick, peeling plaster, rusted steel grilles. Light: daylight through tall windows (about 6000 K), bright; interior is bounce-lit. Shad: soft with window-grid patterns. Atm: haze in the light shafts. Col: warm beige-grey. Cam: 24 mm, slight downward tilt. Story: a looted lobby, a "NO ENTRY / STAFF ONLY" sign. Detail: a radiator under the window, papers on the floor.

**05 — Soviet metro / service level.** Cat: underground transit, industrial. Comp: a column splits the frame; stairs and railing lead toward a lit platform. P/S/T: concrete columns / railings, signage ("ЛИФТ") / boxes, cables. Mat: rough board-formed concrete, painted steel railings, wet tiles. Light: tungsten wall lamps (about 2800 K) in pools; cold ambient. Shad: hard pools. Atm: mild haze. Col: purple-blue shadows, amber pools. Cam: 1.7 m, 20 mm. Story: an abandoned transit node. Detail: an extension cord snaking across the floor, warning signs.

**06 — Sci-fi bunker corridor.** Cat: industrial sci-fi facility. Comp: perfect one-point perspective, symmetrical. P/S/T: panelled walls / ceiling pipes, hazard stripes / vents, labels. Mat: painted concrete panels, yellow-black hazard paint, steel grates. Light: overhead strips plus amber indicator lights. Col: grey-green with yellow accents, mid key. Story: low (clean facility). It shows how *repeated modules* can still read as designed when trim and stripes break up the rhythm.

**07 — Overgrown factory hall (balcony view).** Cat: industrial ruin with nature reclaiming it. Comp: hanging chains in the foreground, a pit and pipes in the midground, light from above. P/S/T: chains / mossy machine, pipes / ferns, rubble. Mat: brick, moss over painted green steel, rust. Light: diffuse skylight from above (warm haze). Atm: thick warm haze. Col: olive and brown, low key. Story: decades of abandonment.

**08 — Hospital waiting corridor (night).** Cat: modern hospital, horror. Comp: diagonal perspective; blue waiting seats in the foreground, a glazed wall on the right. P/S/T: seat bench / glazed partition, dropped ceiling with missing tiles / debris, caution sign, papers, wall-mounted sanitiser. Mat: blue perforated plastic seats, chrome, vinyl floor, damaged ceiling tiles. Light: a few surviving wall lamps (about 4500 K) and a dark plenum. Shad: soft. Atm: slight haze. Col: blue-grey, low key. Cam: 1.5 m, 18–20 mm (wide distortion). Story: evacuation; the ceiling has given way. Detail: cables hanging from the ceiling, a wall clock, hand rails.

**09 — Hospital corridor in daylight.** Cat: Japanese-style hospital ward corridor. Comp: one-point perspective with a window wall on the left and doors and notice boards on the right. P/S/T: corridor shell / IV stands, carts, wet-floor sign / ceiling debris, papers, room numbers. Geo: suspended ceiling with collapsed tiles, structural bulkheads, venetian blinds, handrail. Mat: off-white walls, blistered paint, vinyl floor covered in debris. Light: overcast daylight through windows (about 6000 K), bright; the far window blows out. Shad: soft. Atm: light haze. Col: neutral warm-grey, mid key. Cam: 1.6 m, 18 mm. Story: patients moved out in a hurry. Detail: a room-number plate ("1"), a notice board with documents.

**10 — Overgrown factory floor.** Cat: industrial ruin. Comp: a mossy roller machine is the hero; a light shaft falls from the upper right. Mat: brick, moss, rusted pipes, wet concrete, ferns. Light: a warm sun shaft (about 4500 K). Atm: dusty god rays. Col: warm olive. Story: nature reclaiming the space.

**11 — Cable-reel vignette in dry grass.** Cat: industrial exterior prop shot. Comp: low camera, a hero cable reel, bokeh background. Mat: weathered plywood reels with a rusted metal plate, stenciled text. Light: low warm sun. Col: warm grass against dark steel. Cam: about 0.3 m, 35–50 mm. Detail: stenciled lettering, splinters, rust bleeding from the bolts.

**12 — Underground ribbed tunnel.** Cat: military/industrial tunnel. Comp: a column with "6" in the foreground; a ribbed vault recedes. Mat: rusted steel ribs, worn yellow floor stripes, cobweb masses. Light: a warm sodium lamp (about 2200 K) plus red indicators. Atm: dense in places. Col: amber and teal, very low key. Story: long sealed, spider infestation.

**13 — Bunker corridor with blast door.** Cat: underground facility. Comp: a blast door with a red porthole dominates the right; the corridor recedes on the left. Mat: concrete block, steel door with heavy rivets, chain-link cages, cable bundles. Light: a cold strip light at the wall base, red emergency light. Col: grey-green with red accents. Story: lockdown.

**14 — Soviet lift lobby ("ЛИФТ").** Cat: underground industrial. Comp: a lift gate with a sign is the focal point; a railing leads in from the foreground. Mat: rough concrete, steel mesh gates, sandbags. Light: tungsten (about 2700 K) ceiling and wall lamps. Shad: pooled. Col: amber-brown, low key. Story: a fortified position (sandbags). Detail: a cable coil on the floor.

**15 — Hospital ward hub with graffiti.** Cat: abandoned hospital, horror. Comp: one-point perspective; rows of seats left and right, gurneys with green sheets in the centre. P/S/T: corridor shell / gurneys, seats / graffiti, broken monitor, debris. Mat: dirty plaster with tags, green drapes, chrome. Light: one surviving fluorescent tube (about 5000 K) plus a dim far zone. Atm: haze. Col: sickly yellow-green. Story: occupation after abandonment (graffiti); bodies under sheets.

**16 — Hospital corridor with two-tone walls.** Cat: abandoned hospital. Comp: wide-angle view down the corridor; a bed and a caution sign in the foreground. P/S/T: corridor / hospital bed, caution sign, bins / papers, debris, extinguisher. Geo: mint-green wainscot about 1.0 m high with a cream wall above, lift doors, drop ceiling with vents. Mat: gloss mint paint, cream emulsion, beige floor tiles with grime. Light: daylight from the far end plus cool fill. Col: mint and cream, warm-neutral. Cam: 1.5 m, 16–18 mm. Story: a ward evacuated mid-shift. Detail: a fire extinguisher by the door, recycling bins.

**17 — Hospital corridor in daylight, first-person view.** Cat: Japanese hospital, horror game (the FPS hand and weapon are ignored). Comp: window wall on the left throws striped sun through blinds; the nurse station is on the right. Light: strong sun through blinds (about 5500 K), striped patterns on the floor. Atm: dust in the light. Col: neutral grey, mid key. Story: the nurse station was abandoned. Detail: a calendar, signage in Japanese, a toppled cart.

**18 — Underground "Sector B" corridor.** Cat: facility corridor, horror. Comp: cables sag across the frame; a red toolbox and a fallen tube light glow on the floor. Mat: subway tile, stained, graffiti, stencilled sector letters. Light: a fallen fluorescent tube (cold) and a warm lamp further in. Col: very low key, brown. Story: a struggle or a breach in sector B.

**19 — Hospital corridor in red emergency light.** Cat: hospital, horror. Comp: a gurney under a red sheet in the centre with a wheelchair beside it; a cool fluorescent tube lies on the floor. Light: overwhelming red emergency light (key) against a cyan fluorescent counterpoint. Col: red and black, extremely low key (0.058). Story: the patient was abandoned during the emergency.

## Clusters

| Cluster | Images | Use |
|---|---|---|
| **Hospital corridor** | 02, 08, 09, 15, 16, 17, 19 | Prototype environment (`specs/hospital_corridor.yaml`); target `references/targets/hospital_corridor.json` |
| **Underground facility / tunnel** | 00, 12, 13, 18 (+ 14, 06) | Third environment (`specs/sector_b_service_tunnel.yaml`); target `references/targets/underground_tunnel.json`. Also part of `corridor_horror.json` |
| Soviet metro / service level | 05, 14 | Future spec: columns, railings, tungsten pools |
| Overgrown factory | 04, 07, 10 | Second environment (`specs/overgrown_service_hall.yaml`); target `references/targets/overgrown_industrial.json` |
| Industrial exterior vignettes | 01, 03, 11 | Future spec: terrain plus prop-hero, low camera |

## Construction requirements extracted (hospital corridor prototype)

| Requirement (source images) | Implementation |
|---|---|
| Corridor 2.4–3.0 m wide, suspended ceiling about 2.7 m, structural bays (09, 16) | `layout`: 21 × 2.7 × 2.75 m, 3 m bays, pilasters and ceiling bulkheads every bay |
| Two-tone wall: gloss mint wainscot about 1 m, cream emulsion above, chair rail, rubber skirting (16) | `wainscot_paint` / `plaster_painted` presets with a separate trim geometry |
| Window wall with venetian blinds throwing striped light (09, 17) | `windows` on the left, blinds with per-window states (tilted/raised/broken), a 0.25° sun disc for crisp stripes |
| Drop ceiling with missing and hanging tiles exposing ducts, pipes and cables (08, 09, 15) | Ceiling grid with **collapse zones** driving holes, a drooping T-bar grid, hanging tiles, and a modelled plenum (duct, pipes, cable tray) |
| Debris *under* the ceiling damage, puddles from the leak (00, 09, 16) | `collapse` beat places debris and puddles from the same collapse-zone data |
| Gurney with a sheet, wheelchair, IV pole (02, 15, 19) | `evacuation` beat: gurney beside the ajar ward door, sheet half off, IV toppled |
| Blue perforated waiting seats (08, 15) | `waiting_area` beat (broken seat) |
| Caution sign, papers, notice board, room numbers (08, 09, 16, 17) | `collapse` (sign by the puddle), `paper_trail`, `wall_dressing` (generated plates, posters, documents) |
| Red EXIT sign plus emergency glow at the far end (02, 19) | `practicals`: exit_sign plus a red area light beyond the end doors |
| Fallen fluorescent tube as a practical light (18, 19) | `practicals`: fallen_tube |
| Mostly dead fixtures, one flickering, one hanging (08, 15) | `fixtures.states` |
| Haze that reads in light shafts (09, 17, 04) | Heterogeneous volume, anisotropy 0.55 |
| Wet glossy floor patches (02, 19) | `vinyl_tile_floor.wet` plus puddle water decals |
| Camera 1.5–1.6 m, 18–21 mm, one-point perspective with VP near centre (09, 16, 17) | `camera.shots.hero` (19 mm), plus gurney and reverse coverage shots |
| Low-key, slightly warm, desaturated grade with vignette and grain | `post` block, tuned by the QA loop against the measured target |

## Construction requirements extracted (Sector B service tunnel)

Cluster: 00, 12, 13, 18 for structure, light and mood; 14 and 05 for the sandbag position and the floor cable.
Measured target (`references/targets/underground_tunnel.json`): mean luma about 0.17 (low key, much darker than the
hospital set), RMS contrast about 0.12, warm bias (+0.07), weak haze (dark channel about 0.08): the darkness comes
from **sparse, pooled practicals**, not from fog.

### What the references share (the "rules" of the space)
1. **No daylight at all.** Every photon comes from a fitting that is in frame or just out of it (00, 12, 13, 18).
   The world/sky is therefore black, and the brightness of every area depends on how close it is to a surviving
   lamp. The far end is *not* lit by fog; it is lit by a coloured emergency source (red in 13, 19).
2. **Rhythm comes from the structure.** Ribs, pilasters and downstand beams repeat every 3 m (12's ribbed vault,
   00's recesses). The lamps sit *between* ribs, so each bay is its own pool of light with dark bands between them.
   This alternation is what gives these images depth.
3. **Two-tone walls with a dark dado.** The lower 1.2 m is gloss paint (grey-green in 06/13) that takes the scuffs.
   Above it is limewash or painted brick that flakes in patches (00), and the flaking shows grey mortar and brick,
   never fresh colour.
4. **Services on the walls, not hidden.** A steam main and condensate line on brackets (05, 13); cable bundles on
   J-hooks sagging between them (18), with one span torn loose.
5. **Wayfinding by stencil.** Sector letters and huge bay numbers are sprayed on the walls (12's "6", 18's
   "Sector B"), and worn yellow hazard paint marks the floor edges and thresholds (12).
6. **A story of an occupied, then abandoned, position** (14's sandbags, 18's red toolbox and fallen tube, 05's
   extension cable). Objects cluster where people *did something*; the rest of the tunnel is empty.

### Requirement → implementation
| Requirement (source images) | Implementation |
|---|---|
| Tunnel 2.4–2.8 m wide, flat concrete slab about 3 m up, no suspended ceiling (00, 13) | `layout` 24 × 2.6 × 3.0 m, `ceiling.kind: exposed` with no collapse zones (no roof holes underground) |
| Ribs and pilasters every bay (12, 00) | `pilasters` every 3 m, 0.14 m deep, plus the corridor builder's downstand beams on the same lines |
| Limewashed brick with flakes that show grey (00) | `brick_painted` with the peel under-colour overridden to grey-brown (the first test render showed red under-brick, which read as fresh red brick and was oversaturated against the target) |
| Dark gloss dado band, scuffed (06, 13) | New `tunnel_band` material (spec-defined): grey-green, low roughness, small low-contrast chips, heavy floor grime |
| Caged bulkhead lamps in pools between ribs (00, 05, 14) | New `caged_lamp` practical: cast back box, frosted globe and a **modelled wire guard** that throws barred shadows. The globe is excluded from shadow rays so the bulb is not trapped. Mid-bay positions (y = 4.5, 10.5, 16.5, 19.5) |
| Most lamps dead, colour temperature varies (00 cold, 12 sodium) | Per-lamp `state` and `kelvin`: 4800 K and 4400 K near the camera, one dead, one 2200 K sodium deeper in, one dead wall lamp |
| Red emergency light at the sealed far end (13, 19) | Red caged wall lamp beside the end doors plus a red area light in the zone beyond, seen through the door portholes |
| Fallen fluorescent tube glowing on the floor (18) | `fallen_tube` practical (6500 K) beyond the barricade: a cold counterpoint to the warm and red light |
| Steam main and condensate on brackets (05, 13) | `structures: pipe_run` × 2 on the right wall (one drops to floor level and turns out) |
| Cable bundle on hooks, one span torn (18) | New `cable_sag` structure: hooks on every rib face, 4 cables with individual sag; the span beside the barricade hangs loose |
| Sector letters, bay numbers, hazard bands (12, 18) | New `stencils` beat: generated stencil lettering with bridges, overspray and wear (original artwork); worn yellow/black hazard bands along the right edge and across the threshold before the end doors |
| Sandbag position, toolbox, drums, extension cable (14, 18, 05) | New `breach` beat. A two-row, three-course sandbag line from the left wall (always leaving a walkable gap) and one bag slid off the top. A red toolbox dropped open on the near side with spanners skittered out. Fuel drums against the wall behind the line, one knocked over. An extension cable snaking from a wall socket to the line (the work lamp it fed is gone). Papers blown toward the camera |
| Rubble at the wall base from slow decay, not a collapse (00) | `breach.rubble_at`: small debris fields against alternating walls |
| Low camera for the prop vignette (01, 11 style), one-point view for the hero (00) | `hero`: 1.6 m, 20 mm, VP slightly left of centre. `breach`: 0.75 m, 28 mm, f/2.8 on the toolbox |

### Lessons from the first test renders
- **The lamps sat inside the ceiling beams.** The corridor builder places a downstand beam (z 2.82–3.30 m) on every
  bay line, and lamps placed on bay lines (y = 3, 9, 15, 21) were embedded in them, which made the breach area black.
  The fix was to put the lamps mid-bay, which is also how real fittings are placed. *Rule: practical lights must be
  placed relative to the structure, never on a structural grid line.*
- 30–40 W bulbs are far too weak for Cycles point lights at a 2–3 m throw in a dark tunnel; 110–170 W gives the
  pooled look.

