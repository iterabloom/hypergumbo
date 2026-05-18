// WI-luzuh fixture: Java source-language constructs.
// Triggers: interface, class, annotation, enum, method.

package fixture;

@interface MyAnnotation {
    String value() default "default";
}

public interface IService {
    int run();
}

@MyAnnotation("annotated")
public class MyService implements IService {
    private int count;

    public int run() {
        return count + 1;
    }
}

enum MyEnum {
    ALPHA,
    BETA,
}
