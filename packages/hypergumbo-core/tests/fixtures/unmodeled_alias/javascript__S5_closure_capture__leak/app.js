const fs = require("fs");

function f() {
  let x = "";
  const get = () => x;
  x = process.env.API_KEY;
  console.log(x);
  fs.writeFileSync("o.txt", get());
}
module.exports = { f };
