const fs = require("fs");

function f() {
  const a = {};
  const b = a;
  b.f = process.env.API_KEY;
  console.log(b);
  fs.writeFileSync("o.txt", a.f);
}
module.exports = { f };
