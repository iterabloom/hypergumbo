const express = require("express");
const app = express();

function listUsers(req, res) { res.send("users"); }

app.get("/users", listUsers);
