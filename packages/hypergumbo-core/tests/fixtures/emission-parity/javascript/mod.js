// Emission-parity javascript fixture (ESM — the idiom the strategy notes
// reaches parity; see tests/fixtures/emission-parity/README.md).
import os from 'os';

/** Return a derived string. */
export function helper(value) {
  return os.hostname() + String(value);
}

/** Process items with branching. */
export function process(items, flag) {
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
export const compute = (n) => {
  return process([n], true);
};

export class Service {
  run() {
    return process([1, 2, 3], true);
  }
}

// Entrypoint idiom: top-level executable statement (module run-on-load).
new Service().run();
