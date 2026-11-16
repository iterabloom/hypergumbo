// Covers Symbol.kind 'actor' and 'primitive' (WI-higik): pony.py names a
// type definition's kind after its node type.
actor Main
  new create(env: Env) =>
    env.out.print(Helper.twice(2).string())

primitive Helper
  fun twice(x: U32): U32 => x * 2
