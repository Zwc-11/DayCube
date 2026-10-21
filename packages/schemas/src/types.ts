// Generated from *.schema.json. Run npm run schemas.
export interface Day {
  schema: "daycube.day/1";
  date: string;
  tz: string;
  places: Place[];
  transits: {
    from: string;
    to: string;
    t0: string;
    t1: string;
    mode: "walk" | "vehicle" | "bike" | "unknown";
    conf: number;
  }[];
  /**
   * @maxItems 6
   */
  cube_faces:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string];
  stats: {
    events: number;
    moments: number;
    places: number;
    floor_changes: number;
  };
  summary?: string;
}
export interface Place {
  id: string;
  name: string;
  kind: "house" | "cafe" | "venue" | "outdoor";
  t0: string;
  t1: string;
  lat: number | null;
  lon: number | null;
  osm_id?: string | null;
  venue?: string | null;
  /**
   * @minItems 1
   */
  floors_visited: [number, ...number[]];
  conf: number;
  layer_index: number;
}
export type Event = {
  id: string;
  t: string;
  t_end?: string | null;
  type:
    | "place.visit"
    | "place.floor"
    | "place.transit"
    | "screen.app"
    | "screen.page"
    | "screen.read"
    | "moment.video"
    | "moment.audio"
    | "said.quote"
    | "git.push"
    | "calendar.event";
  label: string;
  place?: string | null;
  floor?: number | null;
  x?: number | null;
  y?: number | null;
  position_fidelity?: "known" | "estimated" | "illustrative";
  source: {
    kind: string;
    ref: string;
    url?: string | null;
    title?: string | null;
  };
  confidence: number;
  fidelity: "known" | "estimated" | "illustrative";
  attention?: number | null;
  people?: string[];
  topics?: string[];
  text?: string;
  moment_id?: string;
  speaker?: string;
  app?: string;
  window_title?: string;
  url?: string;
  title?: string;
  scroll_depth?: number;
  blocks?: string[];
  conf?: number;
  from?: string;
  to?: string;
  mode?: string;
  repo?: string;
  commits?: string[];
  attendees?: string[];
};
export interface Graph {
  nodes: {
    id: string;
    kind: "person" | "place" | "topic" | "moment" | "event";
    label?: string;
  }[];
  edges: {
    from: string;
    to: string;
    rel: string;
    evidence: string[];
  }[];
}
export interface Moment {
  id: string;
  title?: string;
  t0: string;
  duration_s: number;
  lat?: number | null;
  lon?: number | null;
  place: string | null;
  floor?: number | null;
  camera: {
    fov_deg: number;
  };
  fidelity: "known" | "estimated" | "illustrative";
  media_available: boolean;
  consented_people?: string[];
}
export interface Ocr {
  shot: string;
  t: string;
  app: string;
  title: string;
  w: number;
  h: number;
  blocks: {
    id: string;
    /**
     * @minItems 4
     * @maxItems 4
     */
    bbox: [number, number, number, number];
    text: string;
    conf: number;
    attention: number | null;
    dwell_ms: number | null;
  }[];
}
export interface Skeleton {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    geometry: {
      type: "Point";
      /**
       * @minItems 2
       * @maxItems 2
       */
      coordinates: [unknown, unknown];
    };
    properties: {
      t: string;
      alt_rel?: number | null;
      floor: number | null;
      floor_conf?: number;
      place: string | null;
      x: number | null;
      y: number | null;
      pos_conf: number;
      fidelity: "known" | "estimated" | "illustrative";
      source: string;
    };
  }[];
}
export interface Transcript {
  speaker_map: {
    [k: string]: string;
  };
  segments: {
    start: number;
    end: number;
    text: string;
    speaker: string | null;
    words: {
      word: string;
      start: number;
      end: number;
      score?: number;
    }[];
  }[];
}
/**
 * @minItems 2
 * @maxItems 2
 */
export type Segment = [Point, Point];
/**
 * @minItems 2
 * @maxItems 2
 */
export type Point = [number, number];

export interface Venue {
  schema: "daycube.venue/1";
  id: string;
  name: string;
  entrance: {
    lat: number;
    lon: number;
  };
  axis_deg: number;
  floor_height_m: number;
  floors: number;
  levels: {
    index: number;
    walls: Segment[];
    doors: Segment[];
    stairs: [number, number, number, number][];
    rooms: {
      id: string;
      /**
       * @minItems 3
       */
      poly: [Point, Point, Point, ...Point[]];
      kind: string;
    }[];
    graph: {
      nodes: Point[];
      edges: [number, number][];
    };
  }[];
  wifi?: {
    bssid: string;
    x: number;
    y: number;
    floor: number;
    rssi: number;
  }[];
}
