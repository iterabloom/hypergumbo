const express = require("express");
const app = express();
app.get("/users", (req, res) => { res.send("users"); });
