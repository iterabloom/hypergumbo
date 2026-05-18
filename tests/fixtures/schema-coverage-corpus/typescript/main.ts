// WI-luzuh fixture: TypeScript source-language constructs.
// Triggers: interface, type, class, arrow_function, const, enum, namespace.

interface IService {
  run(): number;
}

type StringOrNumber = string | number;

enum Color {
  Red,
  Green,
}

namespace Utils {
  export const greeting: string = "hello";
}

const myConst: number = 42;

const myArrow = (x: number): number => x + 1;

class MyService implements IService {
  count: number = 0;

  run(): number {
    return this.count + 1;
  }
}

export { MyService };
