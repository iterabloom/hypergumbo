# SPDX-License-Identifier: AGPL-3.0-or-later
from starlette.applications import Starlette
from starlette.routing import Route


async def list_users(request):
    return None


routes = [
    Route("/users", list_users, methods=["GET", "POST"]),
]
app = Starlette(routes=routes)
