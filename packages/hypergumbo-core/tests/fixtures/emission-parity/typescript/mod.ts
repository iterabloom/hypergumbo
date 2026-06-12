// Emission-parity typescript fixture (analyzed by the `javascript` analyzer,
// which handles ['javascript', 'typescript', 'vue', 'svelte']).
// See tests/fixtures/emission-parity/README.md.
import * as os from 'os';

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
  if (total > 5) {
    total = 5;
  }
  return helper(total);
}

/** Compute via an arrow function (INV-golap signature probe). */
export const compute = (n: number): string => {
  return process([n], true);
};

export class Service {
  run(): string {
    return process([1, 2, 3], true);
  }
}

// Entrypoint idiom: top-level executable statement (module run-on-load).
new Service().run();
