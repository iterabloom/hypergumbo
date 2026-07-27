// Emission-parity swift fixture.
// See tests/fixtures/emission-parity/README.md.
import Foundation

/// Maximum number of items to process.
let MaxItems = 5

/// Return a derived string.
func helper(_ value: Int) -> String {
    return String(value)
}

/// Process items with branching.
public func process(_ items: [Int], _ flag: Bool) -> String {
    var total = 0
    if flag {
        total += 1
    }
    if !items.isEmpty {
        total += items.count
    }
    if total > 5 {
        total = 5
    }
    return helper(total)
}

/// Service is a small service.
public class Service {
    public var count: Int = 0
    public func run() -> String {
        return process([1, 2, 3], true)
    }
}

let _ = Service().run()
