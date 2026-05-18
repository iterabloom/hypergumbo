// WI-luzuh fixture: C# source-language constructs.
// Triggers: interface, class, struct, enum, property, method.

namespace Fixture;

public interface IService
{
    int Run();
}

public class MyService : IService
{
    public int Count { get; set; }

    public int Run()
    {
        return Count + 1;
    }
}

public struct MyStruct
{
    public int Field;
}

public enum MyEnum
{
    Alpha,
    Beta,
}
