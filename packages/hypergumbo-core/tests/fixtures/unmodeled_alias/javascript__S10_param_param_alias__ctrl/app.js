const fs = require("fs");

function f(a, b) {
  a[0] = process.env.API_KEY;
  console.log(a);
  fs.writeFileSync("o.txt", a[0]);
}

function main() {
  const x = [""];
  f(x, x);
}
module.exports = { f, main };
