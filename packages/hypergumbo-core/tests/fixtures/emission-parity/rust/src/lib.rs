// Emission-parity rust fixture.
// See tests/fixtures/emission-parity/README.md.
use std::cmp::min;

/// Module-level value binding (variable kind).
const MAX_ITEMS: i32 = 5;

fn helper(value: i32) -> String {
    format!("{}", value)
}

/// Process items with branching.
pub fn process(items: &[i32], flag: bool) -> String {
    let mut total = 0;
    if flag {
        total += 1;
    }
    if !items.is_empty() {
        total += items.len() as i32;
    }
    if total > MAX_ITEMS {
        total = min(total, MAX_ITEMS);
    }
    helper(total)
}

/// Config holds a value member (field kind).
pub struct Config {
    pub max: i32,
}

/// Service is a small service.
pub struct Service;

impl Service {
    /// Run runs the service.
    pub fn run(&self) -> String {
        process(&[1, 2, 3], true)
    }
}

fn main() {
    Service.run();
}
