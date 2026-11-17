const fs = require("fs");

function f() {
  let x = "";
  class K { v = x; }
  x = process.env.API_KEY;
  console.log(x);
  fs.writeFileSync("o.txt", x);
}
module.exports = { f };
