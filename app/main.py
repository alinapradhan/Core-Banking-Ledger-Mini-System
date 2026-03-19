from wsgiref.simple_server import make_server

from app.api import JsonApi
from app.service import bootstrap_database

app = JsonApi()


if __name__ == "__main__":
    bootstrap_database()
    with make_server("127.0.0.1", 8000, app) as server:
        print("Serving on http://127.0.0.1:8000")
        server.serve_forever()
