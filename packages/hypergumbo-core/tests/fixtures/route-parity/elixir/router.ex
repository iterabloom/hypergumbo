defmodule MyAppWeb.Router do
  use MyAppWeb, :router

  scope "/", MyAppWeb do
    get "/users", UserController, :index
    post "/users", UserController, :create
  end
end
