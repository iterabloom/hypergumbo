const fs = require("fs");

function f() {
  const s = [""];
  const t = s;
  t[0] = process.env.API_KEY;
  console.log(t);
  fs.writeFileSync("o.txt", s[0]);
}
module.exports = { f };
