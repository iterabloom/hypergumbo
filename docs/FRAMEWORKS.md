# Web/Application Frameworks Reference

This document lists web and application frameworks that hypergumbo can analyze or should consider for pattern detection.

## Currently Supported (with YAML patterns)

See `packages/hypergumbo-core/src/hypergumbo_core/frameworks/*.yaml` for the full list.

### Python
- Django, Flask, FastAPI, Tornado, Aiohttp, Celery, Pyramid, Bottle, Flask-Appbuilder, Flask-RESTful, Sanic, Quart, Falcon, Litestar, Masonite

### JavaScript/Node.js
- Express, Fastify, Koa, Hapi, NestJS, Next.js, Nuxt, Remix, SvelteKit, AdonisJS, Feathers, Restify, Electron, Nex

### Ruby
- Rails, Sinatra, Grape, Hanami, Roda, Padrino

### Java/Kotlin/Scala
- Spring Boot, Micronaut, Quarkus, Ktor, Http4k, Play, Akka HTTP, Scalatra, http4s, JAX-RS (Jersey), Javalin, Vert.x

### PHP
- Laravel, Slim, Symfony, CodeIgniter, Yii, CakePHP, Laminas, FuelPHP, Lumen

### Go
- go-web (covers Gin, Echo, Fiber, Chi, Gorilla Mux, etc.)

### Rust
- rust-web (covers Actix-web, Axum, Rocket, etc.)

### Elixir/Erlang
- Phoenix, Plug, Cowboy

### Swift
- Vapor

### .NET/F#
- ASP.NET Core, Giraffe

### Haskell
- Servant, Scotty

### Clojure
- Ring/Compojure, Pedestal

### R
- Shiny, Plumber

### Lua
- OpenResty

### C++
- Qt

### Cross-language
- CLI patterns (Python, Go, JS, Ruby, Rust), GraphQL, Library Exports

---

## Comprehensive Framework List by Language

### Python Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Django | Full-stack | MTV pattern, ORM, admin |
| Flask | Micro | Werkzeug + Jinja2 |
| FastAPI | Async API | Starlette + Pydantic |
| Starlette | ASGI | Lightweight async |
| Tornado | Async | Non-blocking I/O |
| Bottle | Micro | Single-file framework |
| Pyramid | Full-stack | Flexible, scalable |
| Falcon | API | Minimal, fast REST APIs |
| Sanic | Async | Flask-like, async native |
| Aiohttp | Async | Client/server framework |
| Quart | Async | Async reimagining of Flask |
| Litestar | Async | Modern, fast, Pydantic v2 |
| Connexion | API | OpenAPI-first |
| Eve | REST | MongoDB-backed REST |
| Hug | API | Expose APIs across protocols |
| Masonite | Full-stack | Django-inspired, modern |
| CherryPy | Object-oriented | Pythonic web framework |
| Web2py | Full-stack | RAD framework |
| TurboGears | Full-stack | WSGI stack |
| Responder | API | ASGI framework |

### JavaScript/TypeScript Server Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Express | Minimal | De facto standard |
| Fastify | Performance | Fast, schema-based |
| Koa | Next-gen | From Express creators |
| Hapi | Enterprise | Config-driven |
| NestJS | Enterprise | Angular-inspired, TypeScript |
| Restify | REST API | Focused on REST |
| AdonisJS | Full-stack | Laravel-inspired |
| Sails.js | Full-stack | Rails-inspired MVC |
| LoopBack | API | IBM's API framework |
| Feathers | Real-time | Services + real-time |
| Polka | Micro | Express-like, faster |
| Total.js | Full-stack | All-in-one framework |
| Strapi | Headless CMS | Admin panel + API |
| KeystoneJS | CMS | GraphQL-based CMS |
| Meteor | Full-stack | Real-time, isomorphic |
| Moleculer | Microservices | Progressive microservices |
| Midway | Enterprise | IoC container |
| tsoa | TypeScript | OpenAPI + TypeScript |

### JavaScript Frontend Meta-Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Next.js | React | SSR, SSG, API routes |
| Nuxt | Vue | Universal Vue apps |
| Remix | React | Web standards, SSR |
| SvelteKit | Svelte | Full-stack Svelte |
| Gatsby | React | Static site generator |
| Astro | Multi | Island architecture |
| Qwik | Resumable | Zero hydration |
| SolidStart | Solid | Full-stack Solid |
| Analog | Angular | Angular meta-framework |

### JavaScript Frontend Libraries/Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| React | Library | Component-based UI |
| Vue | Framework | Progressive framework |
| Angular | Framework | Full-featured platform |
| Svelte | Compiler | Compile-time framework |
| Solid.js | Library | Fine-grained reactivity |
| Preact | Library | 3KB React alternative |
| Ember.js | Framework | Convention over config |
| Lit | Library | Web components |
| Stencil | Compiler | Web component compiler |
| Alpine.js | Micro | Inline interactivity |
| Stimulus | Library | Hotwire companion |

### Ruby Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Rails | Full-stack | Convention over config |
| Sinatra | Micro | DSL for web apps |
| Hanami | Full-stack | Clean architecture |
| Grape | API | REST-like API micro-framework |
| Roda | Routing | Routing tree framework |
| Padrino | Full-stack | Built on Sinatra |
| Cuba | Micro | Small, fast |
| Camping | Micro | Single-file framework |
| Ramaze | Modular | Simple, light |
| Volt | Reactive | Isomorphic framework |

### PHP Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Laravel | Full-stack | Modern PHP framework |
| Symfony | Enterprise | Reusable components |
| Slim | Micro | PSR-7 micro-framework |
| Lumen | Micro | Laravel's micro-framework |
| CodeIgniter | Full-stack | Lightweight MVC |
| Yii | Full-stack | High-performance |
| CakePHP | Full-stack | RAD framework |
| Laminas | Enterprise | Formerly Zend |
| Phalcon | Extension | C-extension performance |
| FuelPHP | Full-stack | HMVC framework |

### Java Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Spring Boot | Full-stack | Opinionated Spring |
| Spring MVC | MVC | Traditional Spring web |
| Micronaut | Cloud | Compile-time DI |
| Quarkus | Cloud-native | Kubernetes-native |
| Dropwizard | Microservices | Ops-friendly |
| Vert.x | Reactive | Event-driven, polyglot |
| Javalin | Lightweight | Kotlin-friendly |
| Spark Java | Micro | Sinatra-inspired |
| Play | Reactive | Non-blocking |
| Struts | MVC | Legacy but still used |
| Ratpack | Async | Non-blocking HTTP |
| Jersey | REST | JAX-RS reference impl |
| Blade | Lightweight | Simple MVC |
| Wicket | Component | Component-based |
| JSF | Component | JavaServer Faces |
| Vaadin | Full-stack | Java UI framework |
| Grails | Full-stack | Groovy on Spring Boot |

### Kotlin Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Ktor | Async | JetBrains' framework |
| Http4k | Functional | Typesafe, functional |
| Spring (Kotlin) | Full-stack | Spring with Kotlin DSL |
| Javalin | Lightweight | Works well with Kotlin |

### Go Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Gin | Performance | Fast, middleware-rich |
| Echo | Lightweight | High performance |
| Fiber | Express-like | Fasthttp-based |
| Chi | Lightweight | Composable router |
| Gorilla Mux | Router | Powerful routing |
| Buffalo | Full-stack | Rapid development |
| Iris | Performance | Feature-rich |
| Beego | Full-stack | MVC framework |
| Revel | Full-stack | Hot reload |
| Go Kit | Microservices | Toolkit for services |
| Goa | Design-first | Code generation |
| Encore | Backend | DevOps automation |
| Hertz | ByteDance | High-performance |
| GoFrame | Full-stack | Enterprise framework |

### Rust Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Actix-web | Actor | High performance |
| Axum | Tokio | Ergonomic, modular |
| Rocket | Type-safe | Compile-time safety |
| Warp | Composable | Filter-based |
| Tide | Async-std | Minimal, async |
| Poem | Elegant | Full-featured async |
| Salvo | Simple | Fast, simple |
| Gotham | Flexible | Safety + stability |
| Nickel | Express-like | Hyper-based |

### Elixir Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Phoenix | Full-stack | Rails-like, real-time |
| Plug | Composable | Pipeline middleware |
| Raxx | Pure | Typed HTTP interface |
| Sugar | Modular | Extensible |

### Scala Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Play | Reactive | Non-blocking |
| Akka HTTP | Streaming | Actor-based |
| Finatra | Twitter | Built on Finagle |
| Scalatra | Sinatra-like | Tiny, DSL |
| http4s | Typelevel | Pure functional |
| ZIO HTTP | Effect | ZIO-powered |
| Tapir | Docs | Endpoint descriptions |

### .NET/C# Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| ASP.NET Core | Full-stack | Modern, cross-platform |
| Blazor | SPA | C# in browser (WASM) |
| Nancy | Lightweight | Sinatra-inspired |
| ServiceStack | Services | .NET services framework |

### F# Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Giraffe | ASP.NET | Functional web framework |
| Suave | Functional | Simple, compositional |
| Saturn | Opinionated | MVC on Giraffe |

### Swift Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Vapor | Full-stack | Most popular Swift |
| Perfect | Full-stack | Feature-rich |
| Kitura | IBM | Enterprise Swift |
| Smoke | AWS | Lightweight |
| Hummingbird | Lightweight | HTTP/2 support |

### Dart/Flutter Backend Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Angel | Full-stack | Batteries-included |
| Aqueduct | ORM | Database-focused |
| Shelf | Middleware | Dart web middleware |
| Alfred | Lightweight | Type-safe routing |

### Haskell Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Servant | Type-safe | API as types |
| Yesod | Full-stack | Type-safe, performant |
| Scotty | Sinatra-like | Simple, small |
| Snap | Compositional | High-performance |
| Spock | Lightweight | Scotty-like |
| IHP | Full-stack | Batteries-included |

### OCaml Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Dream | Full-stack | Modern, tidy |
| Opium | Sinatra-like | Express-style |

### Clojure Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Ring | Middleware | HTTP abstraction |
| Compojure | Routing | Concise routing |
| Luminus | Full-stack | Batteries-included |
| Pedestal | Interceptors | Interceptor pattern |
| Reitit | Router | Data-driven routing |

### Perl Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Mojolicious | Full-stack | Real-time features |
| Dancer | Sinatra-like | Simple, lightweight |
| Catalyst | MVC | Mature, flexible |

### Crystal Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Kemal | Sinatra-like | Fast, simple |
| Amber | Full-stack | Rails-inspired |
| Lucky | Full-stack | Type-safe |

### Nim Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Jester | Sinatra-like | Simple routing |
| Prologue | Full-stack | Full-featured |

### V Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Vweb | Built-in | V's web module |

### Gleam Web Frameworks
| Framework | Type | Notes |
|-----------|------|-------|
| Wisp | Mist-based | Gleam web framework |
| Mist | Low-level | HTTP server |

---

## Build Tools / Bundlers (commonly associated)

| Tool | Language | Notes |
|------|----------|-------|
| Webpack | JavaScript | Module bundler |
| Vite | JavaScript | Fast build tool |
| Rollup | JavaScript | ES module bundler |
| esbuild | JavaScript | Fast bundler |
| Parcel | JavaScript | Zero-config bundler |
| Turbopack | JavaScript | Rust-based bundler |

---

## Framework Selection Criteria for Hypergumbo Patterns

A framework warrants a dedicated YAML pattern when it:
1. Has distinct routing/decorator patterns (e.g., `@app.route`, `Router.get`)
2. Uses specific lifecycle hooks or middlewares
3. Has meaningful symbol enrichment opportunities (routes, handlers, models)
4. Is used in production by multiple popular open-source projects

## Contributing New Framework Patterns

To add support for a new framework:
1. Create `packages/hypergumbo-core/src/hypergumbo_core/frameworks/<framework>.yaml`
2. Define patterns for routes, handlers, models, etc.
3. Add tests in `packages/hypergumbo-core/tests/test_framework_patterns.py`
4. Update `CHANGELOG.md`

See existing YAML patterns for examples.
