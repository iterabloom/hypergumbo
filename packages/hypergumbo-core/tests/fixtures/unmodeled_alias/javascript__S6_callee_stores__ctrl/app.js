const fs = require("fs");

function keep(r, v) {
  r.k = v;
}

function f() {
  const reg = {};
  const s = [""];
  keep(reg, s);
  s[0] = process.env.API_KEY;
  console.log(s);
  fs.writeFileSync("o.txt", s[0]);
}
module.exports = { f };
