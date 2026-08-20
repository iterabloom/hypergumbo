import Vapor

func routes(_ app: Application) throws {
    app.get("users") { req in
        return "users"
    }
}
