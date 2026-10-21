import {
  Layer,
  MomentData,
  OcrScreenRecord,
  GraphNode,
  GraphEdge,
  TransitSegment,
  VoxelBox,
} from '../types/daycube';

export const PALETTE = {
  lawn: '#9fc29b',
  lawnCafe: '#8eb695',
  courtyard: '#c7b8a1',
  road: '#b9b3a8',
  path: '#ffb454',
  wall: '#f1e4cf',
  wallB: '#e8d2b9',
  wallHall: '#dce3ec',
  wallCafe: '#f5dec9',
  slab: '#d8c3a5',
  slabHall: '#c5cedb',
  tree: '#6faa7a',
  treeAutumn: '#d48b5c',
  trunk: '#8b6b4a',
  desk: '#b48a5a',
  bed: '#9fb7d9',
  lamp: '#ffe9b0',
  plinth: '#6f6152',
  steel: '#64748b',
  monitor: '#2f3a4f',
  screenGlow: '#38bdf8',
  coffeeCounter: '#5c4033',
  sofa: '#7c9082',
  whiteboard: '#f8fafc',
  serverRack: '#1e293b',
  carBody: '#4a6fa5',
};

const box = (
  x: number,
  y: number,
  z: number,
  w: number,
  d: number,
  h: number,
  color: string,
  floor = 0,
  opts?: VoxelBox['opts']
): VoxelBox => ({
  x,
  y,
  z,
  w,
  d,
  h,
  color,
  floor,
  opts: opts || {},
});

/** Footprint of the Engineering Hall's east stairwell, in diorama meters. */
const STAIRWELL = { x: 18.2, y: 4.6, w: 2.6, d: 6.75 };
const STAIR_STEPS = 9;
const STAIR_RUN = STAIRWELL.d / STAIR_STEPS;

export type StageId = 'home' | 'morningCafe' | 'hall' | 'courtyardCafe';

export type StageSpotKind = 'outside' | 'desk' | 'table' | 'counter' | 'rest' | 'landing';

/** Where the character stands for a kind of activity on a stage, and what it faces there. */
export interface StageSpot {
  kind: StageSpotKind;
  floor: number;
  x: number;
  y: number;
  look?: [number, number];
}

export const STAGE_SPOTS: Record<StageId, StageSpot[]> = {
  home: [
    { kind: 'outside', floor: 0, x: 20.0, y: 10.0 },
    { kind: 'desk', floor: 0, x: 15.65, y: 7.7, look: [15.65, 8.85] },
    { kind: 'rest', floor: 0, x: 7.6, y: 9.85, look: [7.6, 11.6] },
    { kind: 'counter', floor: 0, x: 7.6, y: 6.65, look: [7.6, 5.65] },
    { kind: 'table', floor: 0, x: 10.4, y: 10.8, look: [9.6, 12.0] },
  ],
  morningCafe: [
    { kind: 'outside', floor: 0, x: 11.5, y: 16.0 },
    { kind: 'counter', floor: 0, x: 9.8, y: 6.55, look: [9.8, 5.15] },
    { kind: 'table', floor: 0, x: 11.2, y: 7.8, look: [11.2, 9.2] },
  ],
  hall: [
    { kind: 'outside', floor: 0, x: 9.2, y: 16.6 },
    { kind: 'desk', floor: 0, x: 10.1, y: 9.3, look: [10.1, 10.35] },
    { kind: 'landing', floor: 0, x: 19.5, y: 12.4 },
    { kind: 'landing', floor: 2, x: 19.5, y: 3.7 },
    { kind: 'desk', floor: 2, x: 11.8, y: 7.0, look: [11.8, 5.85] },
    { kind: 'table', floor: 2, x: 7.1, y: 7.1, look: [7.1, 5.9] },
  ],
  courtyardCafe: [
    { kind: 'outside', floor: 0, x: 14.5, y: 17.3 },
    { kind: 'counter', floor: 0, x: 7.3, y: 8.75, look: [5.95, 8.75] },
    { kind: 'table', floor: 0, x: 13.2, y: 9.1, look: [15.1, 9.1] },
  ],
};

export function buildStageBoxes(stage: StageId, evening = false): VoxelBox[] {
  switch (stage) {
    case 'home':
      return buildHomeBoxes(evening);
    case 'morningCafe':
      return buildMorningCafeBoxes();
    case 'hall':
      return buildEngineeringHallBoxes();
    case 'courtyardCafe':
      return buildCourtyardCafeBoxes();
  }
}

// ----------------------------------------------------------------------------
// SCENE 1 & 5: HOME DIORAMA (Exact reference architecture + rich interior props)
// ----------------------------------------------------------------------------
export function buildHomeBoxes(isEvening: boolean): VoxelBox[] {
  const B: VoxelBox[] = [];
  // Floor slab
  B.push(box(5, 4, 0, 13, 10, 0.3, PALETTE.slab, 0));
  const t = 0.25,
    x = 5,
    y = 4,
    w = 13,
    d = 10,
    z = 0.3,
    h = 1.2;
  // Dollhouse walls (east door gap)
  B.push(box(x + w - t, y + t, z, t, 1.5, h, PALETTE.wall, 0, { wall: true }));
  B.push(box(x + w - t, y + 4.5, z, t, d - 4.5 - t, h, PALETTE.wall, 0, { wall: true }));
  B.push(box(x, y + t, z, t, d - 2 * t, h, PALETTE.wall, 0, { wall: true }));
  B.push(box(x, y, z, w, t, h, PALETTE.wall, 0, { wall: true }));
  B.push(box(x, y + d - t, z, w, t, h, PALETTE.wall, 0, { wall: true }));
  // Interior partitions
  B.push(box(5.5, 4.6, 0.3, 4, 0.25, 1.0, PALETTE.wallB, 0, { wall: true }));
  B.push(box(11.5, 4.6, 0.3, 0.25, 4, 1.0, PALETTE.wallB, 0, { wall: true }));
  // Bed + pillow + blanket
  B.push(box(6, 10.5, 0.3, 3.2, 2.2, 0.7, PALETTE.bed, 0, { label: 'Bed' }));
  B.push(box(6, 10.5, 1.0, 1.0, 2.2, 0.25, '#f3f1ea', 0));
  // Nightstand + warm bedside lamp
  B.push(box(6, 9.6, 0.3, 0.7, 0.7, 0.65, PALETTE.desk, 0));
  B.push(box(6.15, 9.75, 0.95, 0.4, 0.4, 0.4, PALETTE.lamp, 0, { lamp: true }));
  // Study Desk + Monitor + Chair
  B.push(box(14.6, 8.3, 0.3, 2.4, 1.1, 0.75, PALETTE.desk, 0, { label: 'Workstation' }));
  B.push(box(15.2, 8.5, 1.05, 0.9, 0.12, 0.6, PALETTE.monitor, 0, { monitor: true }));
  B.push(box(15.25, 8.48, 1.1, 0.8, 0.04, 0.5, PALETTE.screenGlow, 0, { lamp: true }));
  B.push(box(15.3, 9.6, 0.3, 0.7, 0.7, 0.55, '#475569', 0));
  // Kitchen / Dining Island + stools
  B.push(box(8.5, 11.5, 0.3, 2.2, 1.0, 0.8, PALETTE.desk, 0, { label: 'Kitchen Counter' }));
  B.push(box(6.2, 5.2, 0.3, 2.8, 0.9, 0.82, '#c8b7a6', 0)); // Espresso counter
  B.push(box(7.0, 5.35, 1.12, 0.5, 0.45, 0.45, '#334155', 0)); // Espresso machine
  // Living lounge rug + bookshelf
  B.push(box(12.2, 5.0, 0.3, 2.2, 0.4, 1.1, PALETTE.desk, 0));
  // Driveway
  B.push(box(19, 0.5, 0, 7, 17, 0.05, PALETTE.road, 0));
  // Stepping stones from driveway to east door
  B.push(box(18.1, 6.2, 0.02, 0.7, 1.2, 0.06, '#cfc7bc', 0));
  // Parked EV car on driveway in evening / morning
  if (isEvening) {
    B.push(box(21.2, 4.0, 0.05, 2.2, 4.2, 0.65, PALETTE.carBody, 0, { label: 'Vehicle' }));
    B.push(box(21.4, 4.7, 0.7, 1.8, 2.4, 0.55, '#6488b8', 0, { glass: true }));
  }
  // Trees: trunk + two canopy cubes
  for (const [tx, ty] of [
    [2, 2],
    [2, 15],
    [21, 15],
    [23, 3],
  ]) {
    B.push(box(tx + 0.3, ty + 0.3, 0, 0.5, 0.5, 1.3, PALETTE.trunk, 0));
    B.push(box(tx - 0.4, ty - 0.4, 1.3, 1.9, 1.9, 1.5, PALETTE.tree, 0));
    B.push(box(tx - 0.05, ty - 0.05, 2.8, 1.2, 1.2, 0.9, PALETTE.tree, 0));
  }
  // Street lamp post
  B.push(box(12, 16.5, 0, 0.3, 0.3, 3, '#8c8f99', 0));
  B.push(box(11.8, 16.3, 3, 0.7, 0.7, 0.5, PALETTE.lamp, 0, { lamp: true }));
  return B;
}

// ----------------------------------------------------------------------------
// SCENE 2: BLUE BOTTLE CAFÉ (Morning Coffee & Reading, 08:55 - 09:18)
// ----------------------------------------------------------------------------
export function buildMorningCafeBoxes(): VoxelBox[] {
  const B: VoxelBox[] = [];
  // Street & sidewalk along south edge
  B.push(box(0, 14.2, 0, 26, 3.8, 0.05, PALETTE.road, 0));
  B.push(box(0, 12.4, 0, 26, 1.8, 0.12, '#d6cfc4', 0));
  // Café main floor slab
  B.push(box(4, 3, 0, 16, 9.4, 0.3, '#e2ceb5', 0));
  const t = 0.25,
    x = 4,
    y = 3,
    w = 16,
    d = 9.4,
    z = 0.3,
    h = 1.35;
  // Back & side walls
  B.push(box(x, y, z, w, t, h, PALETTE.wallCafe, 0, { wall: true }));
  B.push(box(x, y, z, t, d, h, PALETTE.wallCafe, 0, { wall: true }));
  B.push(box(x + w - t, y, z, t, d, h, PALETTE.wallCafe, 0, { wall: true }));
  // Front wall with wide entrance opening
  B.push(box(x, y + d - t, z, 5.5, t, h, PALETTE.wallCafe, 0, { wall: true }));
  B.push(box(x + 9.5, y + d - t, z, 6.5, t, h, PALETTE.wallCafe, 0, { wall: true }));

  // Long artisan espresso bar counter
  B.push(box(5.5, 4.5, 0.3, 8.5, 1.3, 0.9, PALETTE.coffeeCounter, 0, { label: 'Espresso Bar' }));
  B.push(box(7.0, 4.7, 1.2, 1.4, 0.8, 0.55, '#cbd5e1', 0)); // La Marzocco machine
  B.push(box(11.2, 4.6, 1.2, 1.8, 0.9, 0.5, '#e2e8f0', 0, { glass: true })); // Pastry case

  // Warm pendant lamps over counter
  for (const lx of [6.8, 9.5, 12.2]) {
    B.push(box(lx, 5.0, 2.4, 0.45, 0.45, 0.35, PALETTE.lamp, 0, { lamp: true }));
  }

  // Café tables & chairs
  for (const [tx, ty] of [
    [6.5, 8.5],
    [10.5, 8.5],
    [14.5, 8.5],
    [16.2, 5.5],
  ]) {
    B.push(box(tx, ty, 0.3, 1.4, 1.4, 0.72, PALETTE.desk, 0));
    B.push(box(tx - 0.5, ty + 0.3, 0.3, 0.45, 0.6, 0.5, '#64748b', 0));
    B.push(box(tx + 1.45, ty + 0.3, 0.3, 0.45, 0.6, 0.5, '#64748b', 0));
  }

  // Outdoor sidewalk trees & lamp
  for (const [tx, ty] of [
    [1.5, 2.5],
    [22.0, 3.0],
    [22.5, 9.5],
  ]) {
    B.push(box(tx + 0.3, ty + 0.3, 0, 0.5, 0.5, 1.3, PALETTE.trunk, 0));
    B.push(box(tx - 0.4, ty - 0.4, 1.3, 1.9, 1.9, 1.5, PALETTE.tree, 0));
    B.push(box(tx - 0.05, ty - 0.05, 2.8, 1.2, 1.2, 0.9, PALETTE.tree, 0));
  }
  B.push(box(18.5, 13.0, 0, 0.3, 0.3, 3.0, '#8c8f99', 0));
  B.push(box(18.3, 12.8, 3.0, 0.7, 0.7, 0.45, PALETTE.lamp, 0, { lamp: true }));
  return B;
}

// ----------------------------------------------------------------------------
// SCENE 3: ENGINEERING HALL (Multi-Floor Venue: Floor 0 Lobby + Floor 2 Lab)
// ----------------------------------------------------------------------------
export function buildEngineeringHallBoxes(): VoxelBox[] {
  const B: VoxelBox[] = [];
  // Approach plaza
  B.push(box(1.5, 14.5, 0, 23, 3.0, 0.06, PALETTE.road, 0));

  // ==================== FLOOR 0 (z = 0.0 to 1.5) ====================
  const x = 3.5,
    y = 2.5,
    w = 19,
    d = 11.5,
    t = 0.28;
  B.push(box(x, y, 0, w, d, 0.3, PALETTE.slabHall, 0, { label: 'Floor 0 Lobby Slab' }));
  // Outer walls Floor 0 (entrance gap at south wall x=7..10)
  B.push(box(x, y, 0.3, w, t, 1.2, PALETTE.wallHall, 0, { wall: true }));
  B.push(box(x, y, 0.3, t, d, 1.2, PALETTE.wallHall, 0, { wall: true }));
  B.push(box(x + w - t, y, 0.3, t, d, 1.2, PALETTE.wallHall, 0, { wall: true }));
  B.push(box(x, y + d - t, 0.3, 4.0, t, 1.2, PALETTE.wallHall, 0, { wall: true }));
  B.push(box(x + 7.5, y + d - t, 0.3, w - 7.5, t, 1.2, PALETTE.wallHall, 0, { wall: true }));
  // Corridor partition Floor 0
  B.push(box(x + 0.3, y + 5.5, 0.3, 12.0, 0.25, 1.0, '#cbd5e1', 0, { wall: true }));
  // Lobby reception desk
  B.push(box(8.5, 9.8, 0.3, 3.2, 1.1, 0.85, PALETTE.desk, 0, { label: 'Lobby Reception' }));
  // East stairwell: nine steps from the lobby floor (top 0.3) up to the Floor 2 slab (top 4.48),
  // climbing north. The last step is flush with the slab and closes the stairwell opening.
  const z2 = 4.2;
  const rise = (z2 + 0.28 - 0.3) / STAIR_STEPS;
  for (let s = 0; s < STAIR_STEPS; s++) {
    B.push(
      box(
        STAIRWELL.x,
        STAIRWELL.y + STAIRWELL.d - (s + 1) * STAIR_RUN,
        0.3 + s * rise,
        STAIRWELL.w,
        STAIR_RUN,
        rise,
        '#94a3b8',
        s < 5 ? 0 : 2,
        { stair: true, label: s === 4 ? 'Stairwell Climb (Δh +6.4m)' : undefined }
      )
    );
  }

  // ==================== FLOOR 2 (z = 4.2m elevated!) ====================
  // The slab is four pieces around the stairwell opening so the stairs stay visible.
  const sx0 = STAIRWELL.x;
  const sx1 = STAIRWELL.x + STAIRWELL.w;
  const sy0 = STAIRWELL.y;
  const sy1 = STAIRWELL.y + STAIRWELL.d;
  B.push(box(x, y, z2, sx0 - x, d, 0.28, '#dbeafe', 2, { label: 'Floor 2 Systems Lab Slab' }));
  B.push(box(sx1, y, z2, x + w - sx1, d, 0.28, '#dbeafe', 2));
  B.push(box(sx0, y, z2, STAIRWELL.w, sy0 - y, 0.28, '#dbeafe', 2));
  B.push(box(sx0, sy1, z2, STAIRWELL.w, y + d - sy1, 0.28, '#dbeafe', 2));
  // Railings on the open sides of the stairwell.
  B.push(box(sx0 - 0.12, sy0, z2 + 0.28, 0.12, STAIRWELL.d + 0.12, 0.5, PALETTE.steel, 2));
  B.push(box(sx0, sy1, z2 + 0.28, STAIRWELL.w, 0.12, 0.5, PALETTE.steel, 2));
  B.push(box(sx1, sy0, z2 + 0.28, 0.12, STAIRWELL.d + 0.12, 0.5, PALETTE.steel, 2));
  // Dollhouse perimeter walls on Floor 2
  B.push(box(x, y, z2 + 0.28, w, t, 1.15, PALETTE.wallHall, 2, { wall: true }));
  B.push(box(x, y, z2 + 0.28, t, d, 1.15, PALETTE.wallHall, 2, { wall: true }));
  B.push(box(x + w - t, y, z2 + 0.28, t, d, 1.15, PALETTE.wallHall, 2, { wall: true }));
  B.push(box(x, y + d - t, z2 + 0.28, w, t, 0.55, PALETTE.wallHall, 2, { wall: true })); // low front parapet for visibility
  // Lab glass partition wall
  B.push(box(13.5, y + 0.3, z2 + 0.28, 0.22, 7.2, 1.0, '#93c5fd', 2, { glass: true, wall: true }));

  // Systems Lab Desks & Dual Monitors (where GitHub PR #12 was opened at (12, 6))
  B.push(box(10.4, 5.2, z2 + 0.28, 2.8, 1.3, 0.76, PALETTE.desk, 2, { label: 'Lab Desk (PR #12)' }));
  B.push(box(10.9, 5.35, z2 + 1.04, 0.85, 0.12, 0.55, PALETTE.monitor, 2, { monitor: true }));
  B.push(box(11.9, 5.35, z2 + 1.04, 0.85, 0.12, 0.55, PALETTE.monitor, 2, { monitor: true }));
  B.push(box(10.95, 5.48, z2 + 1.08, 0.75, 0.04, 0.45, PALETTE.screenGlow, 2, { lamp: true }));
  B.push(box(11.95, 5.48, z2 + 1.08, 0.75, 0.04, 0.45, PALETTE.screenGlow, 2, { lamp: true }));

  // Secondary robotics bench
  B.push(box(5.5, 5.2, z2 + 0.28, 3.2, 1.4, 0.76, PALETTE.desk, 2, { label: 'Hardware Bench' }));
  // Server rack in corner
  B.push(box(4.2, 3.1, z2 + 0.28, 1.2, 1.2, 1.8, PALETTE.serverRack, 2, { label: 'GPU Cluster Rack' }));
  B.push(box(4.35, 4.31, z2 + 1.5, 0.9, 0.05, 0.15, '#22c55e', 2, { lamp: true }));
  // Whiteboard on north wall
  B.push(box(7.5, 2.8, z2 + 0.6, 4.5, 0.12, 0.9, PALETTE.whiteboard, 2, { label: 'Hinge Loop Whiteboard' }));

  // Campus trees outside
  for (const [tx, ty] of [
    [0.8, 3.0],
    [0.8, 11.5],
    [23.2, 4.0],
    [23.2, 11.5],
  ]) {
    B.push(box(tx + 0.3, ty + 0.3, 0, 0.5, 0.5, 1.4, PALETTE.trunk, 0));
    B.push(box(tx - 0.4, ty - 0.4, 1.4, 1.9, 1.9, 1.6, PALETTE.tree, 0));
  }
  return B;
}

// ----------------------------------------------------------------------------
// SCENE 4: COURTYARD CAFÉ (Lunch Sync with Sam & Maya, 12:15 - 13:45)
// ----------------------------------------------------------------------------
export function buildCourtyardCafeBoxes(): VoxelBox[] {
  const B: VoxelBox[] = [];
  // Stone courtyard patio slab
  B.push(box(3.5, 2.5, 0, 19, 13, 0.28, PALETTE.courtyard, 0));
  // Indoor roastery pavilion on west side
  B.push(box(4.0, 3.0, 0.28, 6.5, 12.0, 0.15, '#e7d8c5', 0));
  B.push(box(4.0, 3.0, 0.43, 6.5, 0.25, 1.3, PALETTE.wallCafe, 0, { wall: true }));
  B.push(box(4.0, 3.0, 0.43, 0.25, 12.0, 1.3, PALETTE.wallCafe, 0, { wall: true }));
  B.push(box(4.0, 14.75, 0.43, 6.5, 0.25, 1.3, PALETTE.wallCafe, 0, { wall: true }));
  // Roastery bar
  B.push(box(5.2, 5.0, 0.43, 1.5, 7.5, 0.9, PALETTE.coffeeCounter, 0, { label: 'Roastery Counter' }));

  // Courtyard tables with parasols (Central table at (14.5, 8.5) is where Sam & user sit!)
  const tables: [number, number, boolean][] = [
    [14.2, 8.2, true],
    [12.5, 4.5, false],
    [17.8, 5.2, false],
    [17.2, 11.5, false],
  ];
  for (const [tx, ty, main] of tables) {
    B.push(
      box(tx, ty, 0.28, 1.8, 1.8, 0.74, PALETTE.desk, 0, {
        label: main ? 'Demo Sync Table (with Sam)' : undefined,
      })
    );
    // Parasol pole + canopy
    B.push(box(tx + 0.8, ty + 0.8, 1.02, 0.2, 0.2, 1.5, PALETTE.trunk, 0));
    B.push(
      box(tx - 0.3, ty - 0.3, 2.5, 2.4, 2.4, 0.3, main ? '#f59e0b' : '#f3f1ea', 0, {
        lamp: main,
      })
    );
    // Chairs
    B.push(box(tx - 0.55, ty + 0.5, 0.28, 0.45, 0.8, 0.52, '#475569', 0));
    B.push(box(tx + 1.9, ty + 0.5, 0.28, 0.45, 0.8, 0.52, '#475569', 0));
  }

  // Companion character: Sam seated at (16.25, 8.8)
  B.push(box(16.05, 8.65, 0.28, 0.5, 0.5, 0.55, '#1e3a8a', 0)); // legs
  B.push(box(15.95, 8.55, 0.83, 0.7, 0.55, 0.85, '#0d9488', 0, { label: 'Sam (Collaborator)' })); // teal jacket
  B.push(box(15.9, 8.5, 1.68, 0.8, 0.75, 0.75, '#f0c9a5', 0)); // head
  B.push(box(15.88, 8.48, 2.3, 0.85, 0.8, 0.28, '#451a03', 0)); // hair

  // Lush courtyard perimeter planters & olive/autumn trees
  for (const [tx, ty, autumn] of [
    [1.2, 2.0, false],
    [1.2, 14.0, true],
    [22.8, 2.5, true],
    [22.8, 13.5, false],
  ] as [number, number, boolean][]) {
    B.push(box(tx + 0.3, ty + 0.3, 0, 0.5, 0.5, 1.3, PALETTE.trunk, 0));
    B.push(
      box(tx - 0.4, ty - 0.4, 1.3, 1.9, 1.9, 1.5, autumn ? PALETTE.treeAutumn : PALETTE.tree, 0)
    );
    B.push(
      box(tx - 0.05, ty - 0.05, 2.8, 1.2, 1.2, 0.9, autumn ? PALETTE.treeAutumn : PALETTE.tree, 0)
    );
  }

  // String light posts
  B.push(box(11.5, 14.8, 0.28, 0.25, 0.25, 2.9, '#8c8f99', 0));
  B.push(box(11.3, 14.6, 3.18, 0.65, 0.65, 0.4, PALETTE.lamp, 0, { lamp: true }));
  return B;
}

// ============================================================================
// THE 5 PLACE LAYERS FOR 2026-10-20
// ============================================================================
export const SAMPLE_DAY_LAYERS: Layer[] = [
  {
    index: 0,
    placeId: 'home1',
    name: 'Home · Morning',
    subtitle: 'Westwood Sanctuary · Floor 0',
    kind: 'house',
    t0: 8.0,
    t1: 8.75,
    isoT0: '2026-10-20T08:00:00-07:00',
    isoT1: '2026-10-20T08:45:00-07:00',
    conf: 0.86,
    fidelity: 'known',
    osmId: 'way/98412031',
    venueId: null,
    frame: { lat: 34.0689, lon: -118.4452, axisDeg: 0, size: [26, 18] },
    floors: [0],
    floorHeight: 3.1,
    boxes: buildHomeBoxes(false),
    path: [
      { x: 7.5, y: 11.2, floor: 0, t: 8.0, conf: 0.9, fidelity: 'known', altRel: 0.0 },
      { x: 9.5, y: 11.5, floor: 0, t: 8.18, conf: 0.88, fidelity: 'known', altRel: 0.1 },
      { x: 7.6, y: 5.8, floor: 0, t: 8.32, conf: 0.88, fidelity: 'known', altRel: 0.0 },
      { x: 15.6, y: 8.9, floor: 0, t: 8.5, conf: 0.92, fidelity: 'known', altRel: 0.0 },
      { x: 18.5, y: 6.8, floor: 0, t: 8.68, conf: 0.86, fidelity: 'known', altRel: -0.1 },
      { x: 22.0, y: 8.5, floor: 0, t: 8.75, conf: 0.85, fidelity: 'known', altRel: -0.2 },
    ],
    events: [
      {
        id: 'ev_000101',
        t: 8.05,
        tEnd: 8.2,
        isoTime: '2026-10-20T08:03:00-07:00',
        x: 7.5,
        y: 11.2,
        floor: 0,
        kind: 'pin',
        eventType: 'place.visit',
        label: 'Woke up at Home · Barometer zeroed (0.0m)',
        subtitle: 'Arc GPX + Apple Health step start',
        source: 'skeleton/arc_gpx',
        sourceKind: 'arc_gpx',
        fidelity: 'known',
        confidence: 0.86,
        people: [],
        topics: ['morning'],
        text: 'Visit detected at Home (34.0689, -118.4452), floor 0 baseline established.',
      },
      {
        id: 'ev_000105',
        t: 8.48,
        tEnd: 8.66,
        isoTime: '2026-10-20T08:29:00-07:00',
        x: 15.6,
        y: 8.9,
        floor: 0,
        kind: 'event',
        eventType: 'screen.read',
        label: 'Checked Modal GPU job logs for WhisperX diarization',
        subtitle: 'Terminal · jobs/modal/jobs.py · Dwell 84%',
        source: 'screens/0829_00.jpg',
        sourceKind: 'capture_win',
        app: 'WindowsTerminal.exe',
        screenShotId: '0829_00',
        fidelity: 'known',
        confidence: 0.95,
        attention: 0.84,
        people: [],
        topics: ['modal', 'whisperx', 'demo'],
        text: 'Modal A10G container warm: whisperx large-v3 diarization latency 18.4s on 42s clip.',
      },
    ],
    quotes: [],
    moments: [],
    summary: {
      text: 'Started the morning at Home on Floor 0, brewed espresso in the kitchen, and verified overnight Modal A10G WhisperX container runs at the study desk before walking out at 08:45.',
      sentences: [
        {
          text: 'Morning visit at Home began at 08:00 on Floor 0 with barometer baseline re-zeroed.',
          evidence: ['ev_000101'],
          fidelity: 'known',
        },
        {
          text: 'Reviewed Modal GPU job logs for WhisperX diarization at the desk with 0.84 gaze attention.',
          evidence: ['ev_000105'],
          fidelity: 'known',
        },
      ],
    },
    screenShotIds: ['0829_00'],
  },
  {
    index: 1,
    placeId: 'cafe1',
    name: 'Blue Bottle Café',
    subtitle: 'Westwood Blvd · Morning Espresso',
    kind: 'cafe',
    t0: 8.9,
    t1: 9.3,
    isoT0: '2026-10-20T08:54:00-07:00',
    isoT1: '2026-10-20T09:18:00-07:00',
    conf: 0.94,
    fidelity: 'known',
    osmId: 'way/112948102',
    venueId: null,
    frame: { lat: 34.0642, lon: -118.4448, axisDeg: 8, size: [26, 18] },
    floors: [0],
    floorHeight: 3.5,
    boxes: buildMorningCafeBoxes(),
    path: [
      { x: 11.5, y: 13.2, floor: 0, t: 8.9, conf: 0.92, fidelity: 'known', altRel: 0.0 },
      { x: 11.5, y: 10.5, floor: 0, t: 8.95, conf: 0.9, fidelity: 'known', altRel: 0.0 },
      { x: 9.2, y: 6.2, floor: 0, t: 9.02, conf: 0.91, fidelity: 'known', altRel: 0.1 },
      { x: 10.8, y: 8.5, floor: 0, t: 9.1, conf: 0.94, fidelity: 'known', altRel: 0.0 },
      { x: 10.8, y: 8.5, floor: 0, t: 9.25, conf: 0.94, fidelity: 'known', altRel: 0.0 },
      { x: 11.5, y: 13.0, floor: 0, t: 9.3, conf: 0.91, fidelity: 'known', altRel: 0.0 },
    ],
    events: [
      {
        id: 'ev_000112',
        t: 9.08,
        tEnd: 9.24,
        isoTime: '2026-10-20T09:05:00-07:00',
        x: 10.8,
        y: 8.5,
        floor: 0,
        kind: 'moment',
        eventType: 'moment.video',
        label: 'Captured 2.5D Moment: Morning Espresso & Depth Mesh Test',
        subtitle: '42.1s clip · Depth-Anything-V2 metric mesh + WhisperX',
        source: 'moments/mo_001/scene.json',
        sourceKind: 'whisperx',
        momentId: 'mo_001',
        fidelity: 'known',
        confidence: 0.96,
        people: ['person:maya'],
        topics: ['depth', 'diorama', 'coffee'],
        text: 'Recorded 2.5D keyframe test at the café table with Maya discussing the ±25° orbit clamp.',
      },
      {
        id: 'ev_000115',
        t: 9.15,
        isoTime: '2026-10-20T09:09:00-07:00',
        x: 10.8,
        y: 8.5,
        floor: 0,
        kind: 'quote',
        eventType: 'said.quote',
        label: 'Maya: "Clamping the 2.5D orbit to 25 degrees hides the depth edge stretch completely."',
        subtitle: 'WhisperX Diarized · SPEAKER_01 → Maya',
        source: 'transcript:mo_001:seg1',
        sourceKind: 'whisperx',
        momentId: 'mo_001',
        fidelity: 'known',
        confidence: 0.94,
        people: ['person:maya'],
        topics: ['depth', 'orbit'],
        text: 'Clamping the 2.5D orbit to 25 degrees hides the depth edge stretch completely.',
      },
    ],
    quotes: [
      {
        id: 'q_mo001_1',
        t: 9.15,
        isoTime: '2026-10-20T09:09:00-07:00',
        who: 'Maya',
        diarLabel: 'SPEAKER_01',
        confirmed: true,
        them: true,
        side: 'left',
        text: 'Clamping the 2.5D orbit to 25 degrees hides the depth edge stretch completely.',
        evidence: 'transcript:mo_001:seg1',
        momentId: 'mo_001',
        x: 10.8,
        y: 8.5,
        floor: 0,
      },
      {
        id: 'q_mo001_2',
        t: 9.18,
        isoTime: '2026-10-20T09:11:00-07:00',
        who: 'You',
        diarLabel: 'SPEAKER_00',
        confirmed: true,
        them: false,
        side: 'right',
        text: 'Agreed—and keeping the median depth at 2.5 meters makes the café table parallax feel super natural.',
        evidence: 'transcript:mo_001:seg2',
        momentId: 'mo_001',
        x: 10.8,
        y: 8.5,
        floor: 0,
      },
    ],
    moments: ['mo_001'],
    summary: {
      text: 'Stopped at Blue Bottle Café for morning coffee with Maya and captured Moment mo_001 testing the Depth-Anything-V2 2.5D displaced mesh and ±25° orbit clamp.',
      sentences: [
        {
          text: 'Recorded 2.5D Moment mo_001 at the central café table with Maya at 09:05.',
          evidence: ['ev_000112'],
          fidelity: 'known',
        },
        {
          text: 'Maya noted that clamping the 2.5D orbit to ±25° hides stretched border triangles.',
          evidence: ['ev_000115', 'transcript:mo_001:seg1'],
          fidelity: 'known',
        },
      ],
    },
    screenShotIds: [],
  },
  {
    index: 2,
    placeId: 'hall',
    name: 'Engineering Hall',
    subtitle: 'Venue Pack · Floors 0 & 2 · Systems Lab',
    kind: 'venue',
    t0: 9.33,
    t1: 11.75,
    isoT0: '2026-10-20T09:20:00-07:00',
    isoT1: '2026-10-20T11:45:00-07:00',
    conf: 0.91,
    fidelity: 'known',
    osmId: 'way/123456',
    venueId: 'venues/engineering-hall.json',
    frame: { lat: 34.0694, lon: -118.443, axisDeg: 12.0, size: [26, 18] },
    floors: [0, 2],
    floorHeight: 3.2,
    boxes: buildEngineeringHallBoxes(),
    venueLevels: [
      {
        index: 0,
        name: 'Ground Lobby & East Stairwell',
        walls: [
          [
            [3.5, 2.5],
            [22.5, 2.5],
          ],
          [
            [22.5, 2.5],
            [22.5, 14.0],
          ],
          [
            [3.5, 2.5],
            [3.5, 14.0],
          ],
        ],
        doors: [
          [
            [7.5, 14.0],
            [11.0, 14.0],
          ],
        ],
        stairs: [[18.2, 4.6, 2.6, 6.75]],
        rooms: [
          {
            id: 'lobby',
            name: 'Main Entrance Lobby',
            poly: [
              [3.5, 8.0],
              [17.5, 8.0],
              [17.5, 14.0],
              [3.5, 14.0],
            ],
            kind: 'lobby',
          },
        ],
        graph: {
          nodes: [
            [9.2, 13.5],
            [9.2, 10.5],
            [14.5, 10.5],
            [19.2, 10.2],
          ],
          edges: [
            [0, 1],
            [1, 2],
            [2, 3],
          ],
        },
      },
      {
        index: 2,
        name: 'Floor 2 · Robotics & Embodied AI Lab',
        walls: [
          [
            [3.5, 2.5],
            [22.5, 2.5],
          ],
          [
            [13.5, 2.8],
            [13.5, 10.0],
          ],
        ],
        doors: [
          [
            [13.5, 10.0],
            [13.5, 12.0],
          ],
        ],
        stairs: [[18.2, 4.6, 2.6, 6.75]],
        rooms: [
          {
            id: 'lab204',
            name: 'Systems Lab 204',
            poly: [
              [3.5, 2.5],
              [13.5, 2.5],
              [13.5, 14.0],
              [3.5, 14.0],
            ],
            kind: 'lab',
          },
        ],
        graph: {
          nodes: [
            [19.2, 4.8],
            [16.0, 7.5],
            [13.5, 10.5],
            [12.0, 6.0],
            [7.5, 6.0],
          ],
          edges: [
            [0, 1],
            [1, 2],
            [2, 3],
            [3, 4],
          ],
        },
      },
    ],
    wifiBeacons: [
      { bssid: 'aa:bb:cc:01:02:01', ssid: 'eduroam-f0-lobby', x: 9.2, y: 10.5, floor: 0, rssi: -54 },
      { bssid: 'aa:bb:cc:02:04:08', ssid: 'eduroam-f2-lab', x: 12.0, y: 6.0, floor: 2, rssi: -49 },
      { bssid: 'aa:bb:cc:02:04:09', ssid: 'eduroam-f2-stair', x: 18.5, y: 5.5, floor: 2, rssi: -61 },
    ],
    path: [
      { x: 9.2, y: 14.8, floor: 0, t: 9.33, conf: 0.92, fidelity: 'known', altRel: 0.0 },
      { x: 9.2, y: 10.8, floor: 0, t: 9.4, conf: 0.85, fidelity: 'estimated', altRel: 0.1 },
      { x: 14.8, y: 10.5, floor: 0, t: 9.46, conf: 0.84, fidelity: 'estimated', altRel: 0.2 },
      { x: 19.2, y: 9.8, floor: 0, t: 9.5, conf: 0.88, fidelity: 'estimated', altRel: 1.4 },
      { x: 19.2, y: 5.2, floor: 2, t: 9.54, conf: 0.91, fidelity: 'known', altRel: 6.4 },
      { x: 14.5, y: 9.5, floor: 2, t: 9.58, conf: 0.82, fidelity: 'estimated', altRel: 6.3 },
      { x: 12.0, y: 6.0, floor: 2, t: 9.63, conf: 0.91, fidelity: 'known', altRel: 6.4 },
      { x: 12.0, y: 6.0, floor: 2, t: 10.6, conf: 0.93, fidelity: 'known', altRel: 6.4 },
      { x: 7.5, y: 6.2, floor: 2, t: 10.95, conf: 0.87, fidelity: 'estimated', altRel: 6.5 },
      { x: 12.0, y: 6.0, floor: 2, t: 11.4, conf: 0.91, fidelity: 'known', altRel: 6.4 },
      { x: 19.2, y: 9.8, floor: 0, t: 11.7, conf: 0.89, fidelity: 'known', altRel: 0.2 },
    ],
    events: [
      {
        id: 'ev_000120',
        t: 9.54,
        isoTime: '2026-10-20T09:32:24-07:00',
        x: 19.2,
        y: 5.2,
        floor: 2,
        kind: 'pin',
        eventType: 'place.floor',
        label: 'Climbed to Floor 2 · Δh = +6.4m (2 floors × 3.2m)',
        subtitle: 'Barometer + Apple Health 2 flights climbed agreement · conf 0.91',
        source: 'skeleton/floors.py',
        sourceKind: 'barometer',
        fidelity: 'estimated',
        confidence: 0.91,
        people: [],
        topics: ['floor', 'barometer', 'calibration'],
        text: 'Floor transition Floor 0 → Floor 2 detected via 15s median-filtered barometer (+6.4m) with 10s hysteresis and HealthKit flights climbed confirmation.',
      },
      {
        id: 'ev_000123',
        t: 9.636,
        tEnd: 10.266,
        isoTime: '2026-10-20T09:38:10-07:00',
        x: 12.0,
        y: 6.0,
        floor: 2,
        kind: 'event',
        eventType: 'screen.page',
        label: 'Opened GitHub pull request #12 · Add hinge inference loop',
        subtitle: 'chrome.exe · github.com/org/repo/pull/12 · Attention 0.71',
        source: 'screens/0938_10.jpg',
        sourceKind: 'browser_ext',
        url: 'https://github.com/daycube/repo/pull/12',
        app: 'chrome.exe',
        screenShotId: '0938_10',
        fidelity: 'known',
        confidence: 0.98,
        attention: 0.71,
        people: ['person:sam'],
        topics: ['demo', 'hinge', 'barometer'],
        text: 'Add hinge inference loop · pull request #12 · Hysteresis threshold 0.6*H for >= 10s prevents elevator pressure transients from triggering false floor flips.',
      },
      {
        id: 'ev_000126',
        t: 10.85,
        tEnd: 11.05,
        isoTime: '2026-10-20T10:51:00-07:00',
        x: 7.5,
        y: 6.2,
        floor: 2,
        kind: 'moment',
        eventType: 'moment.video',
        label: 'Captured Gaussian Splat Moment: Floor 2 Lab Bench & Sensor Rig',
        subtitle: 'Tier 2 Gaussian Splat (scene.ply) · 7000 steps on Modal A10G',
        source: 'moments/mo_002/scene.json',
        sourceKind: 'whisperx',
        momentId: 'mo_002',
        fidelity: 'known',
        confidence: 0.95,
        people: ['person:sam'],
        topics: ['splat', 'calibration', 'hinge'],
        text: 'Recorded 3D Gaussian Splat of the Floor 2 robotics bench while verifying Viterbi HMM corridor snapping.',
      },
      {
        id: 'ev_000128',
        t: 11.35,
        isoTime: '2026-10-20T11:21:00-07:00',
        x: 12.0,
        y: 6.0,
        floor: 2,
        kind: 'event',
        eventType: 'git.push',
        label: 'Pushed 3 commits to main · feat(skeleton): barometer hysteresis + Viterbi HMM',
        subtitle: 'post-push git hook · commit 8f92c1a',
        source: 'capture/2026-10-20/git.jsonl',
        sourceKind: 'git_hook',
        fidelity: 'known',
        confidence: 0.99,
        people: [],
        topics: ['hinge', 'barometer', 'demo'],
        text: 'git.push to daycube/main: 8f92c1a "feat(skeleton): barometer 0.6H hysteresis + Viterbi corridor map matching".',
      },
    ],
    quotes: [
      {
        id: 'q_mo002_1',
        t: 10.88,
        isoTime: '2026-10-20T10:53:00-07:00',
        who: 'Sam',
        diarLabel: 'SPEAKER_01',
        confirmed: true,
        them: true,
        side: 'left',
        text: 'With the 0.6H barometer hysteresis over ten seconds, that elevator pressure spike on Floor 2 completely disappeared.',
        evidence: 'transcript:mo_002:seg1',
        momentId: 'mo_002',
        x: 7.5,
        y: 6.2,
        floor: 2,
      },
    ],
    moments: ['mo_002'],
    summary: {
      text: 'Entered Engineering Hall Floor 0 lobby at 09:20, climbed the east stairwell to Floor 2 (+6.4m barometer delta), reviewed GitHub PR #12 on hinge inference with 0.71 gaze attention, captured Gaussian Splat Moment mo_002 at the hardware bench, and pushed 3 commits.',
      sentences: [
        {
          text: 'Climbed from Floor 0 to Floor 2 at 09:32 (Δh = +6.4m, floor_height_m = 3.2m, confidence 0.91).',
          evidence: ['ev_000120'],
          fidelity: 'estimated',
        },
        {
          text: 'Read GitHub Pull Request #12 ("Add hinge inference loop") on Floor 2 at (12.0m, 6.0m) with 0.71 gaze attention.',
          evidence: ['ev_000123'],
          fidelity: 'known',
        },
        {
          text: 'Filmed Gaussian Splat Moment mo_002 at the Floor 2 hardware bench with Sam and pushed commit 8f92c1a.',
          evidence: ['ev_000126', 'ev_000128'],
          fidelity: 'known',
        },
      ],
      failureModeNote:
        'Barometer re-zeroed at 09:20 lobby entry (h0 = 114.2m) to eliminate multi-hour weather drift; 10s hysteresis suppressed elevator pressure transient at 10:14.',
    },
    screenShotIds: ['0938_10'],
  },
  {
    index: 3,
    placeId: 'cafe2',
    name: 'Courtyard Roasters',
    subtitle: 'Verve Patio · Lunch & Demo Sync',
    kind: 'cafe',
    t0: 12.25,
    t1: 13.75,
    isoT0: '2026-10-20T12:15:00-07:00',
    isoT1: '2026-10-20T13:45:00-07:00',
    conf: 0.95,
    fidelity: 'known',
    osmId: 'way/33481920',
    venueId: null,
    frame: { lat: 34.0661, lon: -118.4421, axisDeg: -5, size: [26, 18] },
    floors: [0],
    floorHeight: 3.4,
    boxes: buildCourtyardCafeBoxes(),
    path: [
      { x: 14.5, y: 15.2, floor: 0, t: 12.25, conf: 0.94, fidelity: 'known', altRel: 0.0 },
      { x: 7.5, y: 8.5, floor: 0, t: 12.35, conf: 0.91, fidelity: 'known', altRel: 0.0 },
      { x: 14.2, y: 9.0, floor: 0, t: 12.45, conf: 0.95, fidelity: 'known', altRel: 0.0 },
      { x: 14.2, y: 9.0, floor: 0, t: 13.5, conf: 0.95, fidelity: 'known', altRel: 0.0 },
      { x: 15.0, y: 15.2, floor: 0, t: 13.75, conf: 0.93, fidelity: 'known', altRel: 0.0 },
    ],
    events: [
      {
        id: 'ev_000130',
        t: 12.668,
        tEnd: 12.78,
        isoTime: '2026-10-20T12:40:05-07:00',
        x: 14.2,
        y: 9.0,
        floor: 0,
        kind: 'moment',
        eventType: 'moment.video',
        label: 'Decided the Live Demo Order with Sam at Courtyard Table',
        subtitle: 'Moment mo_003 · 2.5D Scene + HRTF Spatial Audio',
        source: 'moments/mo_003/scene.json',
        sourceKind: 'whisperx',
        momentId: 'mo_003',
        fidelity: 'known',
        confidence: 0.97,
        people: ['person:sam', 'person:maya'],
        topics: ['demo', 'demo order', 'shelf', 'teleport'],
        text: 'Decided the demo order at Courtyard Roasters: 1) 7-day Cube Shelf, 2) Unfold Tuesday diorama & scrub to Floor 2, 3) Live judge walk + ask teleport.',
      },
      {
        id: 'ev_000131',
        t: 12.668,
        isoTime: '2026-10-20T12:40:05-07:00',
        x: 14.2,
        y: 9.0,
        floor: 0,
        kind: 'quote',
        eventType: 'said.quote',
        label: 'Sam: "Let’s lock the demo order: start on the cube shelf, unfold Engineering Hall Floor 2, then run the live ask teleport."',
        subtitle: 'WhisperX Diarized · SPEAKER_00 → Sam (Confirmed)',
        source: 'transcript:mo_003:seg3',
        sourceKind: 'whisperx',
        momentId: 'mo_003',
        fidelity: 'known',
        confidence: 0.97,
        people: ['person:sam'],
        topics: ['demo', 'demo order'],
        text: "Let's lock the demo order: start on the cube shelf, unfold Engineering Hall Floor 2, then run the live ask teleport.",
      },
    ],
    quotes: [
      {
        id: 'q_mo003_1',
        t: 12.65,
        isoTime: '2026-10-20T12:39:52-07:00',
        who: 'You',
        diarLabel: 'SPEAKER_01',
        confirmed: true,
        them: false,
        side: 'right',
        text: 'Should we show the raw SQLite FTS5 and vec0 retrieval scores before or after the camera teleports?',
        evidence: 'transcript:mo_003:seg2',
        momentId: 'mo_003',
        x: 14.2,
        y: 9.0,
        floor: 0,
      },
      {
        id: 'q_mo003_2',
        t: 12.668,
        isoTime: '2026-10-20T12:40:05-07:00',
        who: 'Sam',
        diarLabel: 'SPEAKER_00',
        confirmed: true,
        them: true,
        side: 'left',
        text: "Let's lock the demo order: start on the cube shelf, unfold Engineering Hall Floor 2, then run the live ask teleport.",
        evidence: 'transcript:mo_003:seg3',
        momentId: 'mo_003',
        x: 14.2,
        y: 9.0,
        floor: 0,
      },
    ],
    moments: ['mo_003'],
    summary: {
      text: 'Met Sam and Maya for lunch at Courtyard Roasters (12:15–13:45). At 12:40:05 in Moment mo_003, decided the final stage demo order: open on the 7-day cube shelf, unfold Engineering Hall Floor 2, and finish with the live judge walk and `/ask` teleport.',
      sentences: [
        {
          text: 'At 12:40:05 at Courtyard Roasters, you and Sam decided the live stage demo order.',
          evidence: ['ev_000130', 'transcript:mo_003:seg3'],
          fidelity: 'known',
        },
        {
          text: 'Sam proposed starting on the cube shelf, unfolding Engineering Hall Floor 2, and running the live ask teleport.',
          evidence: ['ev_000131', 'transcript:mo_003:seg3'],
          fidelity: 'known',
        },
      ],
    },
    screenShotIds: [],
  },
  {
    index: 4,
    placeId: 'home2',
    name: 'Home · Evening',
    subtitle: 'Westwood Sanctuary · Golden Hour to Night',
    kind: 'house',
    t0: 13.8,
    t1: 22.0,
    isoT0: '2026-10-20T13:48:00-07:00',
    isoT1: '2026-10-20T22:00:00-07:00',
    conf: 0.92,
    fidelity: 'known',
    osmId: 'way/98412031',
    venueId: null,
    frame: { lat: 34.0689, lon: -118.4452, axisDeg: 0, size: [26, 18] },
    floors: [0],
    floorHeight: 3.1,
    boxes: buildHomeBoxes(true),
    // Exact PATH from the user's diorama reference!
    path: [
      { x: 24.5, y: 9, floor: 0, t: 13.8, conf: 0.92, fidelity: 'known', altRel: 0.0 },
      { x: 16, y: 9, floor: 0, t: 14.1, conf: 0.94, fidelity: 'known', altRel: 0.0 },
      { x: 16, y: 9, floor: 0, t: 17.5, conf: 0.95, fidelity: 'known', altRel: 0.0 },
      { x: 10, y: 12, floor: 0, t: 17.6, conf: 0.91, fidelity: 'known', altRel: 0.0 },
      { x: 10, y: 12, floor: 0, t: 19.4, conf: 0.91, fidelity: 'known', altRel: 0.0 },
      { x: 16, y: 9, floor: 0, t: 19.5, conf: 0.94, fidelity: 'known', altRel: 0.0 },
      { x: 16, y: 9, floor: 0, t: 21.4, conf: 0.94, fidelity: 'known', altRel: 0.0 },
      { x: 8, y: 9, floor: 0, t: 21.6, conf: 0.9, fidelity: 'known', altRel: 0.0 },
      { x: 8, y: 9, floor: 0, t: 22.0, conf: 0.9, fidelity: 'known', altRel: 0.0 },
    ],
    events: [
      {
        id: 'ev_000140',
        t: 17.8,
        tEnd: 19.2,
        isoTime: '2026-10-20T17:48:00-07:00',
        x: 16.0,
        y: 9.0,
        floor: 0,
        kind: 'event',
        eventType: 'screen.read',
        label: 'Evaluated Grounding Verifier & Recall Benchmark Results',
        subtitle: 'VS Code · eval/score_grounding.py · Attention 0.89',
        source: 'screens/1748_00.jpg',
        sourceKind: 'capture_win',
        app: 'Code.exe',
        screenShotId: '1748_00',
        fidelity: 'known',
        confidence: 0.97,
        attention: 0.89,
        people: [],
        topics: ['grounding', 'eval', 'opus'],
        text: 'Ran eval/score_grounding.py across 100 card sentences: 0 uncited claims, 3 unsupported sentences caught and dropped by Haiku judge.',
      },
      {
        id: 'ev_000144',
        t: 18.2,
        isoTime: '2026-10-20T18:12:00-07:00',
        x: 10.0,
        y: 12.0,
        floor: 0,
        kind: 'pin',
        eventType: 'place.visit',
        label: 'Dinner & Evening Tea at Kitchen Island',
        subtitle: 'Home Floor 0 · Sunset transition into night lamp mode',
        source: 'skeleton/visits.py',
        sourceKind: 'arc_gpx',
        fidelity: 'known',
        confidence: 0.91,
        people: [],
        topics: ['evening', 'home'],
        text: 'Evening break at the kitchen counter (10.0m, 12.0m) before returning to the study desk at 19:30.',
      },
      {
        id: 'ev_000148',
        t: 20.4,
        tEnd: 21.3,
        isoTime: '2026-10-20T20:24:00-07:00',
        x: 16.0,
        y: 9.0,
        floor: 0,
        kind: 'event',
        eventType: 'screen.page',
        label: 'Reviewed Recall Experiment Protocol (Counterbalanced A/B)',
        subtitle: 'docs/experiment/protocol.md · Attention 0.78',
        source: 'screens/2024_00.jpg',
        sourceKind: 'browser_ext',
        url: 'http://localhost:3000/eval/protocol',
        app: 'chrome.exe',
        screenShotId: '2024_00',
        fidelity: 'known',
        confidence: 0.96,
        attention: 0.78,
        people: [],
        topics: ['experiment', 'recall', 'eval'],
        text: 'Verified counterbalanced A/B protocol for n=8 volunteers: Timeline list (Interface A) vs DAYCUBE 3D World (Interface B).',
      },
    ],
    quotes: [
      {
        id: 'q_home_1',
        t: 17.8,
        isoTime: '2026-10-20T17:48:00-07:00',
        who: 'You (Voice Note)',
        diarLabel: 'SPEAKER_00',
        confirmed: true,
        them: false,
        side: 'center',
        text: 'Golden hour at 17:48—the grounding verifier just hit zero unsupported claims across all fifty benchmark questions.',
        evidence: 'ev_000140',
        momentId: 'mo_003',
        x: 16.0,
        y: 9.0,
        floor: 0,
      },
    ],
    moments: [],
    summary: {
      text: 'Returned Home in the afternoon, worked at the study desk through golden hour (17:48) verifying the Claude Opus 5.5 + Haiku grounding audit (0 uncited claims), paused in the kitchen for dinner (17:36–19:24), finalized the A/B recall experiment protocol, and retired to the bedroom at 21:36.',
      sentences: [
        {
          text: 'At 17:48 at the Home study desk, ran `eval/score_grounding.py` with 0.89 gaze attention, confirming 0 uncited claims.',
          evidence: ['ev_000140'],
          fidelity: 'known',
        },
        {
          text: 'Took an evening break in the kitchen (17:36–19:24) before reviewing the counterbalanced A/B recall experiment protocol until 21:24.',
          evidence: ['ev_000144', 'ev_000148'],
          fidelity: 'known',
        },
      ],
    },
    screenShotIds: ['1748_00', '2024_00'],
  },
];

export const SAMPLE_TRANSITS: TransitSegment[] = [
  {
    from: 'home1',
    to: 'cafe1',
    fromLayer: 0,
    toLayer: 1,
    t0: 8.75,
    t1: 8.9,
    mode: 'walk',
    conf: 0.89,
    distanceM: 580,
  },
  {
    from: 'cafe1',
    to: 'hall',
    fromLayer: 1,
    toLayer: 2,
    t0: 9.3,
    t1: 9.33,
    mode: 'walk',
    conf: 0.92,
    distanceM: 420,
  },
  {
    from: 'hall',
    to: 'cafe2',
    fromLayer: 2,
    toLayer: 3,
    t0: 11.75,
    t1: 12.25,
    mode: 'walk',
    conf: 0.88,
    distanceM: 490,
  },
  {
    from: 'cafe2',
    to: 'home2',
    fromLayer: 3,
    toLayer: 4,
    t0: 13.75,
    t1: 13.8,
    mode: 'vehicle',
    conf: 0.94,
    distanceM: 1450,
  },
];

// ============================================================================
// MOMENTS DATA (2.5D Displaced Depth Scenes & Tier 2 Gaussian Splats)
// ============================================================================
export const SAMPLE_MOMENTS: Record<string, MomentData> = {
  mo_001: {
    id: 'mo_001',
    title: 'Morning Espresso & 2.5D Depth Mesh Check',
    placeId: 'cafe1',
    placeName: 'Blue Bottle Café',
    layerIndex: 1,
    floor: 0,
    t: 9.08,
    isoT0: '2026-10-20T09:05:00-07:00',
    durationS: 42.1,
    lat: 34.0642,
    lon: -118.4448,
    cameraFovDeg: 62,
    tier: '2.5d',
    meshFile: 'moments/mo_001/scene.glb',
    keyframeId: '00012',
    palette: ['#f5dec9', '#b48a5a', '#38bdf8'],
    speakers: [
      {
        diarLabel: 'SPEAKER_01',
        personId: 'person:maya',
        displayName: 'Maya',
        side: 'left',
        confirmed: true,
        color: '#f472b6',
      },
      {
        diarLabel: 'SPEAKER_00',
        personId: 'person:you',
        displayName: 'You',
        side: 'right',
        confirmed: true,
        color: '#ffb454',
      },
    ],
    transcript: [
      {
        id: 'transcript:mo_001:seg1',
        start: 2.4,
        end: 14.8,
        speaker: 'SPEAKER_01',
        text: 'Clamping the 2.5D orbit to 25 degrees hides the depth edge stretch completely.',
        words: [
          { word: 'Clamping', start: 2.4, end: 3.1, score: 0.98 },
          { word: 'the', start: 3.2, end: 3.4, score: 0.99 },
          { word: '2.5D', start: 3.5, end: 4.2, score: 0.95 },
          { word: 'orbit', start: 4.3, end: 4.9, score: 0.97 },
          { word: 'to', start: 5.0, end: 5.2, score: 0.99 },
          { word: '25', start: 5.3, end: 5.9, score: 0.96 },
          { word: 'degrees', start: 6.0, end: 6.8, score: 0.98 },
          { word: 'hides', start: 7.0, end: 7.6, score: 0.97 },
          { word: 'the', start: 7.7, end: 7.9, score: 0.99 },
          { word: 'depth', start: 8.0, end: 8.6, score: 0.98 },
          { word: 'edge', start: 8.7, end: 9.2, score: 0.96 },
          { word: 'stretch', start: 9.3, end: 10.1, score: 0.97 },
          { word: 'completely.', start: 10.2, end: 11.4, score: 0.99 },
        ],
      },
      {
        id: 'transcript:mo_001:seg2',
        start: 15.2,
        end: 29.0,
        speaker: 'SPEAKER_00',
        text: 'Agreed—and keeping the median depth at 2.5 meters makes the café table parallax feel super natural.',
        words: [
          { word: 'Agreed—and', start: 15.2, end: 16.4, score: 0.95 },
          { word: 'keeping', start: 16.5, end: 17.1, score: 0.98 },
          { word: 'the', start: 17.2, end: 17.4, score: 0.99 },
          { word: 'median', start: 17.5, end: 18.2, score: 0.97 },
          { word: 'depth', start: 18.3, end: 18.9, score: 0.98 },
          { word: 'at', start: 19.0, end: 19.2, score: 0.99 },
          { word: '2.5', start: 19.3, end: 20.0, score: 0.96 },
          { word: 'meters', start: 20.1, end: 20.8, score: 0.98 },
          { word: 'makes', start: 20.9, end: 21.4, score: 0.98 },
          { word: 'the', start: 21.5, end: 21.7, score: 0.99 },
          { word: 'café', start: 21.8, end: 22.3, score: 0.96 },
          { word: 'table', start: 22.4, end: 23.0, score: 0.98 },
          { word: 'parallax', start: 23.1, end: 24.0, score: 0.97 },
          { word: 'feel', start: 24.1, end: 24.6, score: 0.98 },
          { word: 'super', start: 24.7, end: 25.3, score: 0.99 },
          { word: 'natural.', start: 25.4, end: 26.5, score: 0.99 },
        ],
      },
    ],
    summary:
      '256×144 displaced depth mesh generated from sharpest Laplacian keyframe (#00012) via Depth-Anything-V2-Small.',
    faceBlurCount: 2,
    consentedPeople: ['Maya', 'You'],
  },
  mo_002: {
    id: 'mo_002',
    title: 'Floor 2 Lab Bench · Gaussian Splat & Barometer Check',
    placeId: 'hall',
    placeName: 'Engineering Hall (Floor 2)',
    layerIndex: 2,
    floor: 2,
    t: 10.85,
    isoT0: '2026-10-20T10:51:00-07:00',
    durationS: 38.5,
    lat: 34.0694,
    lon: -118.443,
    cameraFovDeg: 65,
    tier: 'splat',
    meshFile: 'moments/mo_002/scene.glb',
    splatFile: 'moments/mo_002/scene.ply',
    keyframeId: '00019',
    palette: ['#38bdf8', '#94a3b8', '#22c55e'],
    speakers: [
      {
        diarLabel: 'SPEAKER_01',
        personId: 'person:sam',
        displayName: 'Sam',
        side: 'left',
        confirmed: true,
        color: '#2dd4bf',
      },
      {
        diarLabel: 'SPEAKER_00',
        personId: 'person:you',
        displayName: 'You',
        side: 'right',
        confirmed: true,
        color: '#ffb454',
      },
    ],
    transcript: [
      {
        id: 'transcript:mo_002:seg1',
        start: 1.5,
        end: 14.2,
        speaker: 'SPEAKER_01',
        text: 'With the 0.6H barometer hysteresis over ten seconds, that elevator pressure spike on Floor 2 completely disappeared.',
        words: [
          { word: 'With', start: 1.5, end: 1.9, score: 0.99 },
          { word: 'the', start: 2.0, end: 2.2, score: 0.99 },
          { word: '0.6H', start: 2.3, end: 3.1, score: 0.94 },
          { word: 'barometer', start: 3.2, end: 4.1, score: 0.97 },
          { word: 'hysteresis', start: 4.2, end: 5.2, score: 0.96 },
          { word: 'over', start: 5.3, end: 5.7, score: 0.99 },
          { word: 'ten', start: 5.8, end: 6.2, score: 0.98 },
          { word: 'seconds,', start: 6.3, end: 7.1, score: 0.98 },
          { word: 'that', start: 7.2, end: 7.5, score: 0.99 },
          { word: 'elevator', start: 7.6, end: 8.4, score: 0.97 },
          { word: 'pressure', start: 8.5, end: 9.2, score: 0.98 },
          { word: 'spike', start: 9.3, end: 9.9, score: 0.97 },
          { word: 'on', start: 10.0, end: 10.2, score: 0.99 },
          { word: 'Floor', start: 10.3, end: 10.8, score: 0.98 },
          { word: '2', start: 10.9, end: 11.2, score: 0.99 },
          { word: 'completely', start: 11.3, end: 12.1, score: 0.98 },
          { word: 'disappeared.', start: 12.2, end: 13.5, score: 0.99 },
        ],
      },
      {
        id: 'transcript:mo_002:seg2',
        start: 15.0,
        end: 28.4,
        speaker: 'SPEAKER_00',
        text: 'And Viterbi map matching snapped the corridor walk to within 1.4 meters of the ground-truth nodes.',
        words: [
          { word: 'And', start: 15.0, end: 15.4, score: 0.99 },
          { word: 'Viterbi', start: 15.5, end: 16.3, score: 0.95 },
          { word: 'map', start: 16.4, end: 16.8, score: 0.98 },
          { word: 'matching', start: 16.9, end: 17.6, score: 0.98 },
          { word: 'snapped', start: 17.7, end: 18.4, score: 0.97 },
          { word: 'the', start: 18.5, end: 18.7, score: 0.99 },
          { word: 'corridor', start: 18.8, end: 19.5, score: 0.98 },
          { word: 'walk', start: 19.6, end: 20.1, score: 0.99 },
          { word: 'to', start: 20.2, end: 20.4, score: 0.99 },
          { word: 'within', start: 20.5, end: 21.0, score: 0.98 },
          { word: '1.4', start: 21.1, end: 21.7, score: 0.96 },
          { word: 'meters.', start: 21.8, end: 22.8, score: 0.98 },
        ],
      },
    ],
    summary:
      'Tier 2 3D Gaussian Splat reconstruction (VGGT COLMAP poses + gsplat 7000 steps) of Systems Lab 204 on Floor 2.',
    faceBlurCount: 1,
    consentedPeople: ['Sam', 'You'],
  },
  mo_003: {
    id: 'mo_003',
    title: 'Courtyard Lunch Sync · Deciding the Stage Demo Order',
    placeId: 'cafe2',
    placeName: 'Courtyard Roasters',
    layerIndex: 3,
    floor: 0,
    t: 12.668,
    isoT0: '2026-10-20T12:40:05-07:00',
    durationS: 46.0,
    lat: 34.0661,
    lon: -118.4421,
    cameraFovDeg: 62,
    tier: '2.5d',
    meshFile: 'moments/mo_003/scene.glb',
    keyframeId: '00024',
    palette: ['#ffb454', '#6faa7a', '#0d9488'],
    speakers: [
      {
        diarLabel: 'SPEAKER_00',
        personId: 'person:sam',
        displayName: 'Sam',
        side: 'left',
        confirmed: true,
        color: '#2dd4bf',
      },
      {
        diarLabel: 'SPEAKER_01',
        personId: 'person:you',
        displayName: 'You',
        side: 'right',
        confirmed: true,
        color: '#ffb454',
      },
    ],
    transcript: [
      {
        id: 'transcript:mo_003:seg1',
        start: 0.8,
        end: 10.5,
        speaker: 'SPEAKER_00',
        text: 'Okay, we have three minutes on stage for the live judge walkthrough.',
        words: [
          { word: 'Okay,', start: 0.8, end: 1.4, score: 0.99 },
          { word: 'we', start: 1.5, end: 1.8, score: 0.99 },
          { word: 'have', start: 1.9, end: 2.3, score: 0.99 },
          { word: 'three', start: 2.4, end: 2.9, score: 0.98 },
          { word: 'minutes', start: 3.0, end: 3.7, score: 0.98 },
          { word: 'on', start: 3.8, end: 4.1, score: 0.99 },
          { word: 'stage', start: 4.2, end: 4.9, score: 0.98 },
          { word: 'for', start: 5.0, end: 5.3, score: 0.99 },
          { word: 'the', start: 5.4, end: 5.7, score: 0.99 },
          { word: 'live', start: 5.8, end: 6.3, score: 0.98 },
          { word: 'judge', start: 6.4, end: 7.0, score: 0.97 },
          { word: 'walkthrough.', start: 7.1, end: 8.2, score: 0.98 },
        ],
      },
      {
        id: 'transcript:mo_003:seg2',
        start: 11.0,
        end: 22.8,
        speaker: 'SPEAKER_01',
        text: 'Should we show the raw SQLite FTS5 and vec0 retrieval scores before or after the camera teleports?',
        words: [
          { word: 'Should', start: 11.0, end: 11.5, score: 0.98 },
          { word: 'we', start: 11.6, end: 11.8, score: 0.99 },
          { word: 'show', start: 11.9, end: 12.4, score: 0.99 },
          { word: 'the', start: 12.5, end: 12.7, score: 0.99 },
          { word: 'raw', start: 12.8, end: 13.3, score: 0.97 },
          { word: 'SQLite', start: 13.4, end: 14.2, score: 0.96 },
          { word: 'FTS5', start: 14.3, end: 15.0, score: 0.95 },
          { word: 'and', start: 15.1, end: 15.3, score: 0.99 },
          { word: 'vec0', start: 15.4, end: 16.0, score: 0.94 },
          { word: 'retrieval', start: 16.1, end: 16.9, score: 0.98 },
          { word: 'scores', start: 17.0, end: 17.7, score: 0.98 },
          { word: 'before', start: 17.8, end: 18.4, score: 0.99 },
          { word: 'or', start: 18.5, end: 18.7, score: 0.99 },
          { word: 'after', start: 18.8, end: 19.3, score: 0.99 },
          { word: 'the', start: 19.4, end: 19.6, score: 0.99 },
          { word: 'camera', start: 19.7, end: 20.3, score: 0.98 },
          { word: 'teleports?', start: 20.4, end: 21.5, score: 0.98 },
        ],
      },
      {
        id: 'transcript:mo_003:seg3',
        start: 23.4,
        end: 39.5,
        speaker: 'SPEAKER_00',
        text: "Let's lock the demo order: start on the cube shelf, unfold Engineering Hall Floor 2, then run the live ask teleport.",
        words: [
          { word: "Let's", start: 23.4, end: 23.9, score: 0.99 },
          { word: 'lock', start: 24.0, end: 24.5, score: 0.98 },
          { word: 'the', start: 24.6, end: 24.8, score: 0.99 },
          { word: 'demo', start: 24.9, end: 25.5, score: 0.99 },
          { word: 'order:', start: 25.6, end: 26.3, score: 0.98 },
          { word: 'start', start: 26.4, end: 27.0, score: 0.99 },
          { word: 'on', start: 27.1, end: 27.3, score: 0.99 },
          { word: 'the', start: 27.4, end: 27.6, score: 0.99 },
          { word: 'cube', start: 27.7, end: 28.3, score: 0.98 },
          { word: 'shelf,', start: 28.4, end: 29.1, score: 0.98 },
          { word: 'unfold', start: 29.2, end: 29.9, score: 0.97 },
          { word: 'Engineering', start: 30.0, end: 30.9, score: 0.98 },
          { word: 'Hall', start: 31.0, end: 31.5, score: 0.99 },
          { word: 'Floor', start: 31.6, end: 32.1, score: 0.99 },
          { word: '2,', start: 32.2, end: 32.6, score: 0.99 },
          { word: 'then', start: 32.7, end: 33.1, score: 0.99 },
          { word: 'run', start: 33.2, end: 33.6, score: 0.99 },
          { word: 'the', start: 33.7, end: 33.9, score: 0.99 },
          { word: 'live', start: 34.0, end: 34.5, score: 0.99 },
          { word: 'ask', start: 34.6, end: 35.1, score: 0.98 },
          { word: 'teleport.', start: 35.2, end: 36.4, score: 0.99 },
        ],
      },
    ],
    summary:
      'Captured at Courtyard Roasters patio table (12:40:05). Three non-consented background patio faces automatically Gaussian-blurred (kernel 31).',
    faceBlurCount: 3,
    consentedPeople: ['Sam', 'Maya', 'You'],
  },
};

// ============================================================================
// SCREEN OCR & GAZE FIXATION RECORDS (Section 2.4 & Section 4)
// ============================================================================
export const SAMPLE_OCR_SCREENS: Record<string, OcrScreenRecord> = {
  '0938_10': {
    shot: '0938_10',
    t: 9.636,
    isoTime: '2026-10-20T09:38:10-07:00',
    placeId: 'hall',
    layerIndex: 2,
    floor: 2,
    app: 'chrome.exe',
    title: 'Pull request #12 · Add hinge inference loop — daycube/repo',
    url: 'https://github.com/daycube/repo/pull/12',
    w: 1920,
    h: 1080,
    scrollDepth: 0.64,
    gazeCalibrated: true,
    valErrorPx: 68,
    readingProbs: {
      reading: 0.87,
      skimming: 0.1,
      notLooking: 0.03,
    },
    blocks: [
      {
        id: 'b0',
        bbox: [120, 140, 1140, 215],
        text: 'Add hinge inference loop #12 · Merged 3 commits into main from feat/barometer-hysteresis',
        conf: 0.96,
        attention: 0.78,
        dwellMs: 4200,
        state: 'reading',
      },
      {
        id: 'b1',
        bbox: [120, 250, 1320, 440],
        text: 'Candidate floor: f = round(Δh / H). Apply hysteresis: switch floors only when |Δh - f_current*H| > 0.6*H for >= 10 s. Confidence averaged with Apple Health flights climbed (+0.15 within ±90 s).',
        conf: 0.94,
        attention: 0.91,
        dwellMs: 8400,
        state: 'reading',
      },
      {
        id: 'b2',
        bbox: [120, 480, 1320, 660],
        text: 'Tier 2 indoor position: pedestrian dead reckoning step events × stride (0.7m) along heading, snapped via Viterbi HMM on the venue corridor graph (sigma = 3m).',
        conf: 0.93,
        attention: 0.71,
        dwellMs: 5100,
        state: 'reading',
      },
      {
        id: 'b3',
        bbox: [1380, 250, 1800, 520],
        text: 'Reviewers: @sam (Approved) · Checks: schema-validate PASS, pytest 28 passed in 1.42s',
        conf: 0.91,
        attention: 0.24,
        dwellMs: 920,
        state: 'skimming',
      },
    ],
    fixations: [
      { fixId: 'fx_01', tOffsetMs: 210, x: 310, y: 175, durationMs: 340, conf: 0.92 },
      { fixId: 'fx_02', tOffsetMs: 590, x: 580, y: 180, durationMs: 410, conf: 0.94 },
      { fixId: 'fx_03', tOffsetMs: 1120, x: 290, y: 310, durationMs: 520, conf: 0.95 },
      { fixId: 'fx_04', tOffsetMs: 1690, x: 540, y: 325, durationMs: 680, conf: 0.96 },
      { fixId: 'fx_05', tOffsetMs: 2420, x: 820, y: 340, durationMs: 610, conf: 0.94 },
      { fixId: 'fx_06', tOffsetMs: 3150, x: 1090, y: 365, durationMs: 490, conf: 0.93 },
      { fixId: 'fx_07', tOffsetMs: 3800, x: 420, y: 540, durationMs: 550, conf: 0.91 },
      { fixId: 'fx_08', tOffsetMs: 4450, x: 760, y: 565, durationMs: 480, conf: 0.92 },
      { fixId: 'fx_09', tOffsetMs: 5100, x: 1540, y: 340, durationMs: 190, conf: 0.88 },
    ],
  },
  '0829_00': {
    shot: '0829_00',
    t: 8.48,
    isoTime: '2026-10-20T08:29:00-07:00',
    placeId: 'home1',
    layerIndex: 0,
    floor: 0,
    app: 'WindowsTerminal.exe',
    title: 'modal run jobs/modal/jobs.py::transcribe',
    w: 1920,
    h: 1080,
    scrollDepth: 1.0,
    gazeCalibrated: true,
    valErrorPx: 72,
    readingProbs: {
      reading: 0.82,
      skimming: 0.14,
      notLooking: 0.04,
    },
    blocks: [
      {
        id: 'b0',
        bbox: [100, 160, 1480, 310],
        text: '✓ Modal App "daycube" deployed · Function transcribe(gpu="A10G", model="whisperx-large-v3") ready.',
        conf: 0.97,
        attention: 0.86,
        dwellMs: 4800,
        state: 'reading',
      },
      {
        id: 'b1',
        bbox: [100, 350, 1480, 560],
        text: 'Diarization output: 2 speakers detected (SPEAKER_00, SPEAKER_01). Word-level alignment score 0.974. Saved transcript.json (18.4s).',
        conf: 0.95,
        attention: 0.82,
        dwellMs: 4100,
        state: 'reading',
      },
    ],
    fixations: [
      { fixId: 'fx_10', tOffsetMs: 300, x: 420, y: 220, durationMs: 490, conf: 0.93 },
      { fixId: 'fx_11', tOffsetMs: 850, x: 840, y: 235, durationMs: 530, conf: 0.95 },
      { fixId: 'fx_12', tOffsetMs: 1500, x: 510, y: 430, durationMs: 610, conf: 0.94 },
      { fixId: 'fx_13', tOffsetMs: 2200, x: 960, y: 455, durationMs: 580, conf: 0.92 },
    ],
  },
  '1748_00': {
    shot: '1748_00',
    t: 17.8,
    isoTime: '2026-10-20T17:48:00-07:00',
    placeId: 'home2',
    layerIndex: 4,
    floor: 0,
    app: 'Code.exe',
    title: 'score_grounding.py — daycube/eval',
    w: 1920,
    h: 1080,
    scrollDepth: 0.45,
    gazeCalibrated: true,
    valErrorPx: 64,
    readingProbs: {
      reading: 0.91,
      skimming: 0.07,
      notLooking: 0.02,
    },
    blocks: [
      {
        id: 'b0',
        bbox: [140, 150, 1520, 340],
        text: 'Grounding Audit (100 sentences across 50 benchmark questions): 0 sentences with zero evidence IDs, 3 unsupported sentences dropped by Claude Haiku 5.5 verifier.',
        conf: 0.98,
        attention: 0.94,
        dwellMs: 7600,
        state: 'reading',
      },
      {
        id: 'b1',
        bbox: [140, 380, 1520, 590],
        text: 'Retrieval Benchmark (RRF FTS5 + sqlite-vec 384-d bge-small-en-v1.5): Top-1 hit rate 88.0% (±5s tolerance), Top-3 hit rate 96.0%, mean retrieval latency 19.4 ms.',
        conf: 0.96,
        attention: 0.85,
        dwellMs: 6100,
        state: 'reading',
      },
    ],
    fixations: [
      { fixId: 'fx_20', tOffsetMs: 180, x: 380, y: 230, durationMs: 620, conf: 0.96 },
      { fixId: 'fx_21', tOffsetMs: 850, x: 790, y: 245, durationMs: 710, conf: 0.97 },
      { fixId: 'fx_22', tOffsetMs: 1620, x: 1180, y: 255, durationMs: 540, conf: 0.95 },
      { fixId: 'fx_23', tOffsetMs: 2300, x: 490, y: 470, durationMs: 590, conf: 0.94 },
      { fixId: 'fx_24', tOffsetMs: 2980, x: 920, y: 485, durationMs: 640, conf: 0.96 },
    ],
  },
  '2024_00': {
    shot: '2024_00',
    t: 20.4,
    isoTime: '2026-10-20T20:24:00-07:00',
    placeId: 'home2',
    layerIndex: 4,
    floor: 0,
    app: 'chrome.exe',
    title: 'Recall Experiment Protocol — Counterbalanced A/B Evaluation',
    url: 'http://localhost:3000/eval/protocol',
    w: 1920,
    h: 1080,
    scrollDepth: 0.82,
    gazeCalibrated: true,
    valErrorPx: 71,
    readingProbs: {
      reading: 0.79,
      skimming: 0.16,
      notLooking: 0.05,
    },
    blocks: [
      {
        id: 'b0',
        bbox: [150, 180, 1490, 390],
        text: 'Recall Experiment (n = 8 consented volunteers): Interface A (Plain Timeline + Search) vs Interface B (DAYCUBE 3D Spatial World). Paired accuracy +26.5% higher and mean answer time 41% faster in DAYCUBE.',
        conf: 0.95,
        attention: 0.78,
        dwellMs: 5400,
        state: 'reading',
      },
    ],
    fixations: [
      { fixId: 'fx_30', tOffsetMs: 240, x: 450, y: 270, durationMs: 510, conf: 0.92 },
      { fixId: 'fx_31', tOffsetMs: 810, x: 890, y: 285, durationMs: 490, conf: 0.93 },
    ],
  },
};

// ============================================================================
// KNOWLEDGE GRAPH (Section 2.6 `graph.json`)
// ============================================================================
export const SAMPLE_GRAPH: { nodes: GraphNode[]; edges: GraphEdge[] } = {
  nodes: [
    { id: 'person:sam', kind: 'person', label: 'Sam', subtitle: 'Systems Lead · 2 Moments', x: 240, y: 170 },
    { id: 'person:maya', kind: 'person', label: 'Maya', subtitle: 'Vision & 2.5D · 2 Moments', x: 220, y: 370 },
    { id: 'place:home', kind: 'place', label: 'Home (Westwood)', subtitle: 'Layers 0 & 4 · Floor 0', layerIndex: 4, t: 17.8, x: 680, y: 410 },
    { id: 'place:cafe1', kind: 'place', label: 'Blue Bottle Café', subtitle: 'Layer 1 · Floor 0', layerIndex: 1, t: 9.08, x: 450, y: 430 },
    { id: 'place:hall', kind: 'place', label: 'Engineering Hall', subtitle: 'Layer 2 · Floors 0 & 2', layerIndex: 2, t: 9.636, x: 520, y: 120 },
    { id: 'place:cafe2', kind: 'place', label: 'Courtyard Roasters', subtitle: 'Layer 3 · Floor 0', layerIndex: 3, t: 12.668, x: 460, y: 270 },
    { id: 'topic:demo', kind: 'topic', label: '#demo-order', subtitle: 'Stage Script & Teleport', x: 720, y: 250 },
    { id: 'topic:hinge', kind: 'topic', label: '#barometer-hysteresis', subtitle: 'Floor 2 Δh +6.4m', x: 740, y: 120 },
    { id: 'mo_001', kind: 'moment', label: 'mo_001 · Café 2.5D Mesh', subtitle: '09:05 · 42.1s', layerIndex: 1, t: 9.08, x: 290, y: 470 },
    { id: 'mo_002', kind: 'moment', label: 'mo_002 · Lab Gaussian Splat', subtitle: '10:51 · 38.5s', layerIndex: 2, t: 10.85, x: 350, y: 80 },
    { id: 'mo_003', kind: 'moment', label: 'mo_003 · Demo Order Sync', subtitle: '12:40 · 46.0s', layerIndex: 3, t: 12.668, x: 340, y: 260 },
    { id: 'ev_000123', kind: 'event', label: 'PR #12 · Hinge Inference', subtitle: '09:38 · Attention 0.71', layerIndex: 2, t: 9.636, x: 620, y: 190 },
    { id: 'ev_000140', kind: 'event', label: 'Grounding Audit (0 drops)', subtitle: '17:48 · Attention 0.89', layerIndex: 4, t: 17.8, x: 820, y: 350 },
  ],
  edges: [
    { from: 'mo_001', to: 'person:maya', rel: 'with', evidence: ['transcript:mo_001:seg1'] },
    { from: 'mo_001', to: 'place:cafe1', rel: 'at', evidence: ['ev_000112'] },
    { from: 'mo_002', to: 'person:sam', rel: 'with', evidence: ['transcript:mo_002:seg1'] },
    { from: 'mo_002', to: 'place:hall', rel: 'at', evidence: ['ev_000126'] },
    { from: 'mo_002', to: 'topic:hinge', rel: 'about', evidence: ['transcript:mo_002:seg1'] },
    { from: 'mo_003', to: 'person:sam', rel: 'with', evidence: ['transcript:mo_003:seg3'] },
    { from: 'mo_003', to: 'person:maya', rel: 'with', evidence: ['ev_000130'] },
    { from: 'mo_003', to: 'place:cafe2', rel: 'at', evidence: ['ev_000130'] },
    { from: 'mo_003', to: 'topic:demo', rel: 'about', evidence: ['transcript:mo_003:seg3'] },
    { from: 'ev_000123', to: 'place:hall', rel: 'at', evidence: ['ev_000123'] },
    { from: 'ev_000123', to: 'topic:hinge', rel: 'about', evidence: ['ev_000123'] },
    { from: 'ev_000123', to: 'topic:demo', rel: 'about', evidence: ['ev_000123'] },
    { from: 'ev_000140', to: 'place:home', rel: 'at', evidence: ['ev_000140'] },
    { from: 'ev_000140', to: 'topic:demo', rel: 'about', evidence: ['ev_000140'] },
  ],
};
