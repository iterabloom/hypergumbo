# SPDX-License-Identifier: AGPL-3.0-or-later
from flask import Flask
from flask_restful import Api, Resource

app = Flask(__name__)
api = Api(app)


class UserList(Resource):
    def get(self):
        return []


api.add_resource(UserList, "/users")
