const fs = require("fs");

let G = [];

function f() {
  const s = [""];
  G = s;
  s[0] = process.env.API_KEY;
  console.log(s);
  fs.writeFileSync("o.txt", G[0]);
}
module.exports = { f };
