import * as fs from "fs";

export function f(): void {
  let x = "";
  class K { v = x; }
  x = process.env.API_KEY as string;
  console.log(x);
  fs.writeFileSync("o.txt", x);
}
