const fs = require("fs");

function f() {
  const inner = [""];
  const reg = {};
  reg.k = inner;
  inner[0] = process.env.API_KEY;
  console.log(inner);
  fs.writeFileSync("o.txt", inner[0]);
}
module.exports = { f };
