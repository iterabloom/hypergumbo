const fs = require("fs");

function f() {
  const inner = [""];
  const outer = [inner];
  inner[0] = process.env.API_KEY;
  console.log(inner);
  fs.writeFileSync("o.txt", inner[0]);
}
module.exports = { f };
