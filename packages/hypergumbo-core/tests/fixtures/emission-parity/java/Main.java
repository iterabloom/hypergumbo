// Emission-parity java fixture.
// See tests/fixtures/emission-parity/README.md.
import java.util.List;

public class Main {
    /** Instance count, default zero-value. */
    int count = 0;

    /** Return a derived string. */
    static String helper(int value) {
        return String.valueOf(value);
    }

    /** Process items with branching. */
    public static String process(List<Integer> items, boolean flag) {
        int total = 0;
        if (flag) {
            total += 1;
        }
        if (!items.isEmpty()) {
            total += items.size();
        }
        if (total > 5) {
            total = 5;
        }
        return helper(total);
    }

    public static void main(String[] args) {
        process(List.of(1, 2, 3), true);
    }
}
