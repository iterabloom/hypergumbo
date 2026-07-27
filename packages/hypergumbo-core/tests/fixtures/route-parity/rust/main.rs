use actix_web::{get, App, HttpServer, Responder};

#[get("/users")]
async fn list_users() -> impl Responder { "users" }
