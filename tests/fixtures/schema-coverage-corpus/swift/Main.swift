// WI-luzuh fixture: Swift source-language constructs.
// Triggers: protocol, class, extension, enum, function.

import Foundation

protocol Runnable {
    func run() -> Int
}

class MyService: Runnable {
    var count: Int = 0

    func run() -> Int {
        return count + 1
    }
}

extension MyService {
    func helper() -> String {
        return "helper"
    }
}

enum MyEnum {
    case alpha
    case beta(Int)
}
