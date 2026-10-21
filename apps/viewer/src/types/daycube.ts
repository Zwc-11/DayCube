export type Fidelity = 'known' | 'estimated' | 'illustrative';

export type PlaceKind = 'house' | 'cafe' | 'venue' | 'outdoor';

export type EventType =
  | 'place.visit'
  | 'place.floor'
  | 'place.transit'
  | 'screen.app'
  | 'screen.page'
  | 'screen.read'
  | 'moment.video'
  | 'moment.audio'
  | 'said.quote'
  | 'git.push'
  | 'calendar.event';

export interface VoxelBox {
  x: number;
  y: number;
  z: number;
  w: number;
  d: number;
  h: number;
  color: string;
  floor?: number;
  opts?: {
    lamp?: boolean;
    glow?: boolean;
    monitor?: boolean;
    glass?: boolean;
    wall?: boolean;
    roof?: boolean;
    label?: string;
    fidelity?: Fidelity;
  };
}

export interface PathPoint {
  x: number;
  y: number;
  floor: number;
  t: number; // hours decimal e.g. 17.8 for 17:48
  conf: number;
  fidelity: Fidelity;
  altRel?: number; // barometric delta h in meters
}

export interface EventPin {
  id: string;
  t: number; // decimal hour (e.g. 9.636)
  tEnd?: number;
  isoTime: string;
  x: number;
  y: number;
  floor: number;
  kind: 'pin' | 'event' | 'moment' | 'quote';
  eventType: EventType;
  label: string;
  subtitle?: string;
  source: string;
  sourceKind: 'browser_ext' | 'capture_win' | 'whisperx' | 'arc_gpx' | 'barometer' | 'git_hook';
  url?: string;
  app?: string;
  fidelity: Fidelity;
  confidence: number;
  attention?: number;
  momentId?: string;
  screenShotId?: string;
  people: string[];
  topics: string[];
  text: string;
  deleted?: boolean;
}

export interface Quote {
  id: string;
  t: number;
  isoTime: string;
  who: string;
  diarLabel: string; // e.g. SPEAKER_00, SPEAKER_01
  confirmed: boolean;
  them: boolean;
  side: 'left' | 'center' | 'right';
  text: string;
  evidence: string;
  momentId: string;
  x: number;
  y: number;
  floor: number;
}

export interface TranscriptSegment {
  id: string;
  start: number; // seconds into moment
  end: number;
  speaker: string; // SPEAKER_00 | SPEAKER_01
  text: string;
  words: { word: string; start: number; end: number; score: number }[];
}

export interface MomentData {
  id: string;
  title: string;
  placeId: string;
  placeName: string;
  layerIndex: number;
  floor: number;
  t: number; // decimal hour
  isoT0: string;
  durationS: number;
  lat: number;
  lon: number;
  cameraFovDeg: number;
  tier: '2.5d' | 'splat';
  meshFile: string;
  splatFile?: string;
  keyframeId: string;
  palette: [string, string, string];
  speakers: {
    diarLabel: string;
    personId: string;
    displayName: string;
    side: 'left' | 'center' | 'right';
    confirmed: boolean;
    color: string;
  }[];
  transcript: TranscriptSegment[];
  summary: string;
  faceBlurCount: number;
  consentedPeople: string[];
}

export interface OcrBlock {
  id: string;
  bbox: [number, number, number, number]; // [x0, y0, x1, y1] in 1920x1080
  text: string;
  conf: number;
  attention: number;
  dwellMs: number;
  state: 'reading' | 'skimming' | 'ignored';
}

export interface GazeFixation {
  fixId: string;
  tOffsetMs: number;
  x: number; // 0..1920
  y: number; // 0..1080
  durationMs: number;
  conf: number;
}

export interface OcrScreenRecord {
  shot: string;
  t: number;
  isoTime: string;
  placeId: string;
  layerIndex: number;
  floor: number;
  app: string;
  title: string;
  url?: string;
  w: number;
  h: number;
  scrollDepth: number;
  gazeCalibrated: boolean;
  valErrorPx: number;
  readingProbs: {
    reading: number;
    skimming: number;
    notLooking: number;
  };
  blocks: OcrBlock[];
  fixations: GazeFixation[];
}

export interface VenueRoom {
  id: string;
  name: string;
  poly: [number, number][];
  kind: 'lobby' | 'office' | 'lab' | 'corridor' | 'cafe' | 'bedroom' | 'kitchen';
}

export interface VenueLevel {
  index: number;
  name: string;
  walls: [[number, number], [number, number]][];
  doors: [[number, number], [number, number]][];
  stairs: [number, number, number, number][]; // [x, y, w, d]
  rooms: VenueRoom[];
  graph: {
    nodes: [number, number][];
    edges: [number, number][];
  };
}

export interface WifiBeacon {
  bssid: string;
  ssid: string;
  x: number;
  y: number;
  floor: number;
  rssi: number;
}

export interface Layer {
  index: number;
  placeId: string;
  name: string;
  subtitle: string;
  kind: PlaceKind;
  t0: number; // decimal hour e.g. 8.0
  t1: number; // decimal hour e.g. 8.75
  isoT0: string;
  isoT1: string;
  conf: number;
  fidelity: Fidelity;
  osmId: string | null;
  venueId: string | null;
  frame: {
    lat: number;
    lon: number;
    axisDeg: number;
    size: [number, number]; // [PW, PD] in meters
  };
  floors: number[];
  floorHeight: number;
  boxes: VoxelBox[];
  venueLevels?: VenueLevel[];
  wifiBeacons?: WifiBeacon[];
  path: PathPoint[];
  events: EventPin[];
  quotes: Quote[];
  moments: string[]; // moment IDs
  summary: {
    text: string;
    sentences: { text: string; evidence: string[]; fidelity: Fidelity }[];
    failureModeNote?: string;
  };
  screenShotIds: string[];
}

export interface TransitSegment {
  from: string;
  to: string;
  fromLayer: number;
  toLayer: number;
  t0: number;
  t1: number;
  mode: 'walk' | 'vehicle' | 'bike';
  conf: number;
  distanceM: number;
}

export interface EvidenceRecord {
  id: string;
  unitId: number;
  type: EventType | 'ocr.block' | 'place.summary';
  t: number;
  isoTime: string;
  placeId: string;
  placeName: string;
  layerIndex: number;
  floor: number;
  x: number;
  y: number;
  text: string;
  sourceRef: string;
  fidelity: Fidelity;
  confidence: number;
  attention?: number;
  rrfScore?: number;
  ftsRank?: number;
  vecRank?: number;
}

export interface AskRewriteOutput {
  terms: string[];
  time_window: [string, string] | null;
  place_hint: string | null;
  people: string[];
  wants: 'moment' | 'screen' | 'place' | 'any';
}

export interface AskResult {
  mode?: 'extractive' | 'standalone-sim';
  hasLocation?: boolean;
  message?: string;
  question: string;
  layerIndex: number;
  t: number;
  floor: number;
  x: number;
  y: number;
  momentId?: string;
  offsetS?: number;
  screenShotId?: string;
  rewrite: AskRewriteOutput;
  latencyMs: {
    rewrite: number;
    retrieve: number;
    phrase: number;
    verify: number;
    total: number;
  };
  verifierAudit: {
    checkedSentences: number;
    droppedSentences: number;
    judgeModel: string;
    status: 'VERIFIED_ALL' | 'PARTIAL_DROP';
  };
  sentences: {
    text: string;
    evidence: string[];
    sharedTokens: string[];
  }[];
  evidence: EvidenceRecord[];
}

export interface GraphNode {
  id: string;
  kind: 'person' | 'place' | 'topic' | 'moment' | 'event';
  label: string;
  subtitle?: string;
  layerIndex?: number;
  t?: number;
  x?: number; // 2D layout pos
  y?: number;
}

export interface GraphEdge {
  from: string;
  to: string;
  rel: 'with' | 'at' | 'about' | 'said_in' | 'read_on';
  evidence: string[];
}

/** One day on the week shelf. Only recorded days can be opened. */
export interface WeekDay {
  date: string;
  recorded: boolean;
  isCurrent: boolean;
}

export interface DeletionAudit {
  id: string;
  t0: number;
  t1: number;
  isoT0: string;
  isoT1: string;
  reason: string;
  appliedAt: string;
  purgedEventsCount: number;
}
