# SPDX-License-Identifier: AGPL-3.0-or-later
from flask import Flask
app = Flask(__name__)


@app.route("/users")
def list_users():
    return "users"
