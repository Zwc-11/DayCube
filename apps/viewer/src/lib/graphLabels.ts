/** Reusable label candidates. Degree order is stable until the visible model changes. */
export class GraphLabels {
  readonly order: Int32Array;
  readonly items: Int32Array;
  readonly counts = new Int32Array(4);
  readonly rank: Uint8Array;
  readonly alpha: Float64Array;
  readonly capacity: number;

  constructor(local: Int32Array, degree: Uint32Array) {
    const n = this.capacity = local.length;
    this.order = new Int32Array(n);
    for (let i = 0; i < n; i++) this.order[i] = i;
    this.order.sort((a, b) => degree[local[b]] - degree[local[a]] || a - b);
    this.items = new Int32Array(n * 4);
    this.rank = new Uint8Array(n);
    this.alpha = new Float64Array(n);
  }

  reset(): void {
    this.counts.fill(0);
  }

  /** Called in `order`: each bucket keeps the degree/tie order without sorting each frame. */
  add(local: number, rank: 0 | 2 | 3 | 4, alpha: number): void {
    const group = rank === 4 ? 0 : rank === 3 ? 1 : rank === 2 ? 2 : 3;
    this.items[group * this.capacity + this.counts[group]++] = local;
    this.rank[local] = rank;
    this.alpha[local] = alpha;
  }
}
