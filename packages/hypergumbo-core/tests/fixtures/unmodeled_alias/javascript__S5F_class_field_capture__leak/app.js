const fs = require("fs");

function f() {
  let x = "";
  class K { v = x; }
  x = process.env.API_KEY;
  console.log(x);
  fs.writeFileSync("o.txt", new K().v);
}
module.exports = { f };
