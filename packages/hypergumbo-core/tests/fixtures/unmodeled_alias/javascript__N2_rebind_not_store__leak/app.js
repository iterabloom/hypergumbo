const fs = require("fs");

function f() {
  const s = [""];
  let t = s;
  t = [process.env.API_KEY];
  console.log(t);
  fs.writeFileSync("o.txt", s[0]);
}
module.exports = { f };
