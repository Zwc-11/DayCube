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
export type Ids = string[];

/**
 * vault/<date>/review.json: the end-of-day summary of a review day (04:00 to 04:00 local). Every number comes from code; the words come from a template or a language model whose output passed the validator. Every cited id is a key of evidence.
 */
export interface Review {
  schema: "daycube.review/1";
  date: string;
  zone: string;
  /**
   * draft: written before the review day ended (23:00, or on request); final: after it.
   */
  kind: "draft" | "final";
  made: string;
  window: {
    t0: string;
    t1: string;
  };
  writer: {
    /**
     * template, or the model that wrote the words.
     */
    name: string;
    attempts: number;
    /**
     * Why the validator rejected a model's answer.
     */
    rejected: string[];
  };
  /**
   * Hash of the facts the review was written from: the same facts are never written twice.
   */
  facts: string;
  headline: string;
  /**
   * At most 8 (the writer and its validator hold to it).
   */
  sentences: Cited[];
  numbers: {
    key: string;
    label: string;
    display: string;
    value: number | null;
    evidence: Ids;
  }[];
  categories: {
    name: string;
    seconds: number;
    share: number;
    display: string;
    evidence: Ids;
  }[];
  /**
   * Phone apps by time, from iOS Shortcuts' opened and closed events (share: of the phone time).
   */
  apps?: {
    name: string;
    seconds: number;
    share: number;
    display: string;
    evidence: Ids;
  }[];
  /**
   * Blocks planned in the daily note's Plan section or the calendar files, against what the day was.
   */
  plan?: {
    text: string;
    status: "done" | "partly" | "elsewhere" | "missed";
    from: string;
    to: string;
    source: "note" | "calendar";
    evidence: Ids;
  }[];
  done: {
    text: string;
    kind: "project" | "commits" | "site" | "read";
    evidence: Ids;
  }[];
  /**
   * At most 3, each tied to a number or a category by its key.
   */
  improvements: {
    text: string;
    metric: string;
    evidence: Ids;
  }[];
  tomorrow: {
    text: string;
    kind: "deadline" | "resume" | "hours";
    /**
     * A deadline's date, or date and local time.
     */
    due: string | null;
    evidence: Ids;
  }[];
  evidence: {
    [k: string]: {
      t: string | null;
      until: string | null;
      day: string | null;
      /**
       * timeline (a segment of the day's timeline) or the type of the search unit.
       */
      kind: string;
      text: string;
      fidelity: "known" | "estimated" | "illustrative";
      /**
       * A segment's first records (capture:app:..., place:...), at most 5.
       */
      records?: string[];
    };
  };
  /**
   * What the review could not see: unrecorded sources, known limits of the day's records.
   */
  gaps: string[];
  /**
   * Some of the records behind it are synthetic or sample data.
   */
  illustrative: boolean;
}
export interface Cited {
  text: string;
  /**
   * @minItems 1
   */
  evidence: [string, ...string[]];
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
/**
 * One line of vault/<date>/timeline.jsonl: a stretch of the review day (04:00 to 04:00 local) spent in one place doing one kind of thing. Time the computer and phone did not record has no segment.
 */
export interface TimelineSegment {
  t: string;
  until: string;
  where: string;
  doing: string;
  /**
   * At the desk only: coding, studying, ... or other.
   */
  category: string | null;
  project: string | null;
  /**
   * Moment ids of conversations recorded during the segment.
   */
  talk: string[];
  /**
   * Times something else was in front for under 20 s and the segment went on.
   */
  switches: number;
  /**
   * Ids of the records the segment was made from: capture:app:<id>, capture:visit:<id>, capture:away:<id>, capture:media:<id>, place:<id>, event ids.
   *
   * @minItems 1
   */
  evidence: [string, ...string[]];
  source: "pc" | "phone" | "pc+phone";
  /**
   * illustrative: made from synthetic or sample records.
   */
  fidelity: "known" | "estimated" | "illustrative";
  /**
   * How the category was decided; null away from the desk.
   */
  labeled: ("correction" | "rule" | "cache" | "llm" | "none") | null;
  /**
   * At the desk: the window or page in front longest (page:<url> or app:<program>|<title>), the key a correction of just it uses.
   */
  key: string | null;
  /**
   * Its site or app (site:<domain> or app:<program>), the key a correction of all of it uses.
   */
  broad: string | null;
}
/**
 * vault/training/<id>/run.json: one training run of a model on this PC (a scan's splats, a research model). Its measurements are vault/training/<id>/metrics.jsonl, one line per step: {"step": n, "t": ISO time, <metric>: number, ...}.
 */
export interface TrainingRun {
  schema: "daycube.training/1";
  id: string;
  /**
   * What is trained: splat, revisit-predictor, ...
   */
  model: string;
  title: string;
  started: string;
  ended: string | null;
  status: "running" | "done" | "failed";
  steps_total: number | null;
  /**
   * The metrics its lines carry: the unit, and which way is better.
   */
  metrics: {
    [k: string]: {
      unit: string;
      /**
       * null: neither, as for a count.
       */
      better: "higher" | "lower" | null;
    };
  };
  /**
   * Where it ran, e.g. RTX 5070.
   */
  device: string | null;
  /**
   * What it learned from: a moment, a dataset.
   */
  source: string | null;
  /**
   * Its final numbers and outputs.
   */
  result: {
    [k: string]: number | string | null;
  };
  error: string | null;
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
export type Shares = {
  name: string;
  seconds: number;
  share: number;
}[];

/**
 * vault/weeks/<YYYY-Www>.json: a week (Monday to Sunday) in numbers, from the days' timelines and reviews. Written once Sunday's review day has ended, and again when a day of the week changes.
 */
export interface Week {
  schema: "daycube.week/1";
  week: string;
  from: string;
  to: string;
  zone: string;
  made: string;
  /**
   * @minItems 7
   * @maxItems 7
   */
  days: [
    {
      date: string;
      desk_s: number;
      focus: number;
      focus_s: number;
      longest_s: number;
      category: string | null;
      project: string | null;
      phone: number | null;
      pickups: number | null;
      commits: number | null;
      keys: number | null;
      /**
       * The day's review, if it has one.
       */
      review: "draft" | "final" | null;
    },
    {
      date: string;
      desk_s: number;
      focus: number;
      focus_s: number;
      longest_s: number;
      category: string | null;
      project: string | null;
      phone: number | null;
      pickups: number | null;
      commits: number | null;
      keys: number | null;
      /**
       * The day's review, if it has one.
       */
      review: "draft" | "final" | null;
    },
    {
      date: string;
      desk_s: number;
      focus: number;
      focus_s: number;
      longest_s: number;
      category: string | null;
      project: string | null;
      phone: number | null;
      pickups: number | null;
      commits: number | null;
      keys: number | null;
      /**
       * The day's review, if it has one.
       */
      review: "draft" | "final" | null;
    },
    {
      date: string;
      desk_s: number;
      focus: number;
      focus_s: number;
      longest_s: number;
      category: string | null;
      project: string | null;
      phone: number | null;
      pickups: number | null;
      commits: number | null;
      keys: number | null;
      /**
       * The day's review, if it has one.
       */
      review: "draft" | "final" | null;
    },
    {
      date: string;
      desk_s: number;
      focus: number;
      focus_s: number;
      longest_s: number;
      category: string | null;
      project: string | null;
      phone: number | null;
      pickups: number | null;
      commits: number | null;
      keys: number | null;
      /**
       * The day's review, if it has one.
       */
      review: "draft" | "final" | null;
    },
    {
      date: string;
      desk_s: number;
      focus: number;
      focus_s: number;
      longest_s: number;
      category: string | null;
      project: string | null;
      phone: number | null;
      pickups: number | null;
      commits: number | null;
      keys: number | null;
      /**
       * The day's review, if it has one.
       */
      review: "draft" | "final" | null;
    },
    {
      date: string;
      desk_s: number;
      focus: number;
      focus_s: number;
      longest_s: number;
      category: string | null;
      project: string | null;
      phone: number | null;
      pickups: number | null;
      commits: number | null;
      keys: number | null;
      /**
       * The day's review, if it has one.
       */
      review: "draft" | "final" | null;
    }
  ];
  totals: {
    desk_s: number;
    focus: number;
    focus_s: number;
    phone: number | null;
    pickups: number | null;
    commits: number | null;
    keys: number | null;
    days_recorded: number;
  };
  categories: Shares;
  projects: Shares;
  /**
   * The week before, for comparison; null when nothing was recorded then.
   */
  previous: {
    desk_s: number;
    focus: number;
    focus_s: number;
  } | null;
  best_hours: {
    from: string;
    to: string;
    stretches: number;
  } | null;
  next: {
    text: string;
    due: string;
  }[];
}
