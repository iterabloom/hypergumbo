// Emission-parity csharp fixture.
// See tests/fixtures/emission-parity/README.md.
using System;
using System.Collections.Generic;

public class Service
{
    /// <summary>Return a derived string.</summary>
    static string Helper(int value)
    {
        return value.ToString();
    }

    /// <summary>Process items with branching.</summary>
    public static string Process(List<int> items, bool flag)
    {
        int total = 0;
        if (flag) { total += 1; }
        if (items.Count > 0) { total += items.Count; }
        if (total > 5) { total = 5; }
        return Helper(total);
    }

    public static void Main(string[] args)
    {
        Process(new List<int> { 1, 2, 3 }, true);
        Console.WriteLine("done");
    }
}
