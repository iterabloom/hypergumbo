// Emission-parity javascript fixture (ESM — the idiom the strategy notes
// reaches parity; see tests/fixtures/emission-parity/README.md).
import os from 'os';

// Module/package-level variable: a top-level const binding at file scope.
const MAX_ITEMS = 5;

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
  if (total > MAX_ITEMS) {
    total = MAX_ITEMS;
  }
  return helper(total);
}

/** Compute via an arrow function (INV-golap signature probe). */
export const compute = (n) => {
  return process([n], true);
};

export class Service {
  // Class field: a value member of the type (zero-value default).
  count = 0;

  run() {
    this.count += 1;
    return process([1, 2, 3], true);
  }
}

// Entrypoint idiom: top-level executable statement (module run-on-load).
new Service().run();
