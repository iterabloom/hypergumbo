// Emission-parity typescript fixture (analyzed by the `javascript` analyzer,
// which handles ['javascript', 'typescript', 'vue', 'svelte']).
// See tests/fixtures/emission-parity/README.md.
import * as os from 'os';

// Module-level value binding (top-level const at file scope).
const MAX_ITEMS: number = 5;

/** Return a derived string. */
export function helper(value: number): string {
  return os.hostname() + String(value);
}

/** Process items with branching. */
export function process(items: number[], flag: boolean): string {
  let total = 0;
  if (flag) {
    total += 1;
  }
  if (items.length) {
    total += items.length;
  }
  if (total > MAX_ITEMS) {
    total = MAX_ITEMS;
  }
  return helper(total);
}

/** Compute via an arrow function (INV-golap signature probe). */
export const compute = (n: number): string => {
  return process([n], true);
};

export class Service {
  count: number = 0;

  run(): string {
    this.count += 1;
    return process([1, 2, 3], true);
  }
}

/** Enumerated type whose named members are container members. */
export enum Color {
  Red = 'red',
  Green = 'green',
}

/** Abstract type whose member signatures are container members. */
export interface Drawable {
  draw(): string;
  area(): number;
}

// Entrypoint idiom: top-level executable statement (module run-on-load).
new Service().run();
