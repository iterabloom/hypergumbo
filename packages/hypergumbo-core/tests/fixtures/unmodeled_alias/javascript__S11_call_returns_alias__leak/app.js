const fs = require("fs");

function view(v) {
  return v;
}

function f() {
  const s = [""];
  const t = view(s);
  t[0] = process.env.API_KEY;
  console.log(t);
  fs.writeFileSync("o.txt", s[0]);
}
module.exports = { f };
