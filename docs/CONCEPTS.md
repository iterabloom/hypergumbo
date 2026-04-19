<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- GENERATED: scripts/generate-concepts (WI-dajul) — do not edit by hand. -->
# Concept Vocabulary Registry

This file enumerates every concept string that the framework-YAML pattern layer emits into `symbol.meta.concepts` and pairs each one with the downstream code that reads it.

- **live** — at least one YAML producer AND at least one code consumer. The concept carries signal end-to-end.
- **inert** — producers exist but no consumer reads the concept. Candidates for either removing the producer pattern or writing a Framework-subcategory linker that consumes it.
- **ghost** — a consumer mentions the concept by name but no YAML emits it. Likely dead code or a leftover reference to a removed pattern; investigate.

Total concepts: **317** (live: 37, inert: 279, ghost: 1).

## Inventory

| Concept | Status | Producers (YAMLs) | Consumers (source paths) |
| --- | --- | --- | --- |
| `action` | inert | yii | _(none)_ |
| `actor` | inert | akka-http | _(none)_ |
| `admin` | inert | django, padrino | _(none)_ |
| `android_component` | inert | config-conventions | _(none)_ |
| `android_permission` | inert | config-conventions | _(none)_ |
| `api_bridge` | inert | electron | _(none)_ |
| `api_contract` | inert | vertx | _(none)_ |
| `api_doc` | inert | javalin, scalatra | _(none)_ |
| `api_handler` | inert | nextjs, nuxt | _(none)_ |
| `api_param` | inert | javalin | _(none)_ |
| `api_resource` | inert | flask, flask-appbuilder, flask-restful | _(none)_ |
| `api_route` | inert | nextjs | _(none)_ |
| `api_view` | inert | flask-appbuilder | _(none)_ |
| `app_bootstrap` | live | react, solid | `entrypoints.py` |
| `application` | live | aiohttp, giraffe, http4s, hummingbird, javalin, openresty, padrino, plumber, qt, roda, scotty, servant, shiny, swiftui, tornado, yesod, zio | `entrypoints.py` |
| `argument` | inert | cli, cli-js, cli-ruby, cli-rust | _(none)_ |
| `asset` | inert | yii | _(none)_ |
| `async` | inert | jax-rs, quarkus, remix, spring-boot, tornado | _(none)_ |
| `audio_context` | inert | web_audio | _(none)_ |
| `audio_data_loading` | inert | web_audio | _(none)_ |
| `audio_framework` | inert | web_audio | _(none)_ |
| `audio_graph_connection` | inert | web_audio | _(none)_ |
| `audio_node_creation` | inert | web_audio | _(none)_ |
| `audio_worklet` | inert | web_audio | _(none)_ |
| `auth` | inert | adonisjs, akka-http, fastapi, flask, flask-appbuilder, giraffe, grape, graphql, ktor, masonite, play, tornado, vapor, yesod | _(none)_ |
| `auth_backend` | inert | django | _(none)_ |
| `auth_config` | inert | aspnet, hapi | _(none)_ |
| `auth_middleware` | inert | aspnet, koa | _(none)_ |
| `auth_strategy` | inert | express, hapi, koa | _(none)_ |
| `authentication` | inert | feathers, fuelphp, laminas, quarkus, servant, symfony, yii | _(none)_ |
| `authorization` | inert | laminas, padrino, quarkus, symfony, yii | _(none)_ |
| `background_service` | inert | aspnet | _(none)_ |
| `background_task` | inert | fastapi | _(none)_ |
| `bean` | inert | guice, jakarta-cdi, spring-boot | _(none)_ |
| `behavior` | inert | cakephp, yii | _(none)_ |
| `benchmark_function` | live | test-frameworks | `entrypoints.py` |
| `bookmark` | inert | shiny | _(none)_ |
| `bootstrap` | inert | yii | _(none)_ |
| `broadcast` | inert | masonite | _(none)_ |
| `broker_lifecycle_by_name` | live | naming-conventions | `entrypoints.py` |
| `build_helper` | inert | language-conventions | _(none)_ |
| `build_macro` | inert | language-conventions | _(none)_ |
| `build_rule` | inert | language-conventions | _(none)_ |
| `cache` | inert | akka-http, aspnet, fuelphp, hapi, laminas, play, yii | _(none)_ |
| `callable_unit` | inert | language-conventions | _(none)_ |
| `cargo_binary` | live | config-conventions | `entrypoints.py` |
| `cargo_build_dependency` | inert | config-conventions | _(none)_ |
| `cargo_dependency` | inert | config-conventions | _(none)_ |
| `cargo_dev_dependency` | inert | config-conventions | _(none)_ |
| `cargo_library` | inert | config-conventions | _(none)_ |
| `cargo_package` | inert | config-conventions | _(none)_ |
| `cargo_workspace_member` | inert | config-conventions | _(none)_ |
| `chart_view` | inert | flask-appbuilder | _(none)_ |
| `circuit_breaker` | inert | vertx | _(none)_ |
| `client` | inert | http4k, http4s, quarkus, restify, vertx | _(none)_ |
| `code_section` | inert | language-conventions | _(none)_ |
| `command` | live | adonisjs, cakephp, cli, cli-go, cli-js, cli-ruby, cli-rust, codeigniter, django, flask, fuelphp, laminas, laravel, lumen, masonite, symfony, vapor, yii | `entrypoints.py`, `linkers/subprocess_cli.py` |
| `command_by_name` | live | naming-conventions | `entrypoints.py` |
| `component` | inert | cakephp, jakarta-cdi, laravel, lit, nuxt, phoenix, react, spring-boot, yesod, yii | _(none)_ |
| `composer_dependency` | inert | config-conventions | _(none)_ |
| `computed` | inert | solid | _(none)_ |
| `config` | inert | codeigniter, feathers, hanami, javalin, micronaut, nuxt, quarkus, sinatra | _(none)_ |
| `configuration` | inert | celery, guice, spring-boot, stapler | _(none)_ |
| `connection` | inert | qt | _(none)_ |
| `content_region` | inert | language-conventions | _(none)_ |
| `context` | inert | http4k, solid | _(none)_ |
| `contract` | inert | http4k | _(none)_ |
| `contract_route` | inert | http4k | _(none)_ |
| `control_flow` | inert | scotty | _(none)_ |
| `controller` | live | adonisjs, aiohttp, aspnet, cakephp, codeigniter, django, falcon, fuelphp, giraffe, grape, hanami, kafka-connect, ktor, laminas, laravel, litestar, lumen, masonite, micronaut, nestjs, padrino, phoenix, play, plug, pyramid, rails, slim, spring-boot, stapler, swiftui, symfony, tornado, vapor, yii | `entrypoints.py`, `linkers/controller_routes.py` |
| `controller_by_name` | live | naming-conventions | `entrypoints.py` |
| `cors` | inert | aspnet, scalatra | _(none)_ |
| `crud_handler` | inert | javalin | _(none)_ |
| `data_fetcher` | inert | nextjs, nuxt, remix, sveltekit | _(none)_ |
| `data_source` | inert | solid | _(none)_ |
| `database` | inert | fuelphp, play, yesod | _(none)_ |
| `decoder` | inert | http4s | _(none)_ |
| `decorator` | inert | rails | _(none)_ |
| `dependency` | inert | aspnet, fastapi, guice | _(none)_ |
| `dependency_injection` | inert | adonisjs | _(none)_ |
| `deployment` | inert | vertx | _(none)_ |
| `dialog` | inert | electron, qt | _(none)_ |
| `document_structure` | inert | language-conventions | _(none)_ |
| `documentation` | inert | aspnet, grape, servant | _(none)_ |
| `dom_query` | inert | lit | _(none)_ |
| `dsl` | inert | http4s | _(none)_ |
| `dto` | inert | vapor | _(none)_ |
| `effect` | inert | solid | _(none)_ |
| `encoder` | inert | http4s | _(none)_ |
| `entity` | live | cakephp, codeigniter | `datamodels.py` |
| `entrypoint` | live | akka-http, cli, cli-go, cli-ruby, electron | `entrypoints.py` |
| `error` | inert | cli | _(none)_ |
| `error_handler` | live | adonisjs, akka-http, bottle, fastapi, fastify, feathers, flask, flask-restful, giraffe, grape, hapi, javalin, jax-rs, ktor, laravel, litestar, lumen, micronaut, nuxt, openresty, padrino, plug, pyramid, quart, remix, restify, ring-compojure, sanic, scalatra, scotty, servant, sinatra, slim, spring-boot, sveltekit, tornado, vertx | `entrypoints.py` |
| `event` | inert | adonisjs, fuelphp, laravel, masonite | _(none)_ |
| `event_config` | inert | lit | _(none)_ |
| `event_consumer` | inert | vertx | _(none)_ |
| `event_handler` | live | celery, django, guice, hapi, jakarta-cdi, laravel, micronaut, nestjs, openresty, pyramid, qt, quarkus, rails, sanic, spring-boot, symfony, yii | `entrypoints.py` |
| `event_listener` | inert | cakephp, fuelphp, laminas, lumen | _(none)_ |
| `event_publisher` | inert | quarkus, rails, vertx | _(none)_ |
| `event_source` | inert | play | _(none)_ |
| `events` | inert | http4k | _(none)_ |
| `example_function` | live | test-frameworks | `entrypoints.py` |
| `extension` | inert | hapi | _(none)_ |
| `factory` | inert | laminas, laravel, micronaut | _(none)_ |
| `federation` | inert | graphql | _(none)_ |
| `file_upload` | inert | scalatra | _(none)_ |
| `filter` | inert | django, laminas, scalatra | _(none)_ |
| `fixture` | inert | cakephp, yii | _(none)_ |
| `flash` | inert | scalatra | _(none)_ |
| `form` | inert | cakephp, django, flask, fuelphp, laminas, laravel, pyramid, rails, remix, sveltekit, symfony, yesod | _(none)_ |
| `form_view` | inert | flask-appbuilder | _(none)_ |
| `formatter` | inert | yii | _(none)_ |
| `gpu_function` | inert | language-conventions | _(none)_ |
| `gpu_kernel` | inert | language-conventions | _(none)_ |
| `graphql_resolver` | live | graphql, graphql-python, graphql-ruby, phoenix | `entrypoints.py` |
| `graphql_schema` | live | graphql, graphql-python, graphql-ruby, phoenix | `entrypoints.py` |
| `grpc_client` | inert | go-web | _(none)_ |
| `grpc_service` | inert | go-web, micronaut | _(none)_ |
| `guard` | inert | nestjs | _(none)_ |
| `halt` | inert | padrino, roda, scalatra | _(none)_ |
| `handler` | inert | cowboy, giraffe, http4k, javalin, laminas, servant, stapler, vertx | _(none)_ |
| `handler_by_name` | live | naming-conventions | `entrypoints.py` |
| `hash_branch` | inert | roda | _(none)_ |
| `hash_route` | inert | roda | _(none)_ |
| `headers` | inert | remix | _(none)_ |
| `health_check` | inert | quarkus | _(none)_ |
| `helper` | inert | cakephp, codeigniter, grape, laminas, padrino, sinatra, symfony | _(none)_ |
| `hook` | inert | react | _(none)_ |
| `host_function` | inert | language-conventions | _(none)_ |
| `http_client` | inert | aiohttp, express, micronaut, play | _(none)_ |
| `hydrator` | inert | laminas | _(none)_ |
| `input` | inert | shiny | _(none)_ |
| `interceptor` | inert | nestjs | _(none)_ |
| `internal_state` | inert | lit | _(none)_ |
| `ipc_handler` | live | electron, tauri | `entrypoints.py` |
| `ipc_sender` | inert | electron | _(none)_ |
| `job` | inert | adonisjs, lumen, masonite, vapor, yii | _(none)_ |
| `json_response` | inert | nex | _(none)_ |
| `json_support` | inert | scalatra | _(none)_ |
| `lens` | inert | http4k | _(none)_ |
| `library` | inert | codeigniter | _(none)_ |
| `library_export` | live | library-exports | `entrypoints.py` |
| `lifecycle` | inert | aiohttp, electron, fastapi, hapi, restify, ring-compojure, solid | _(none)_ |
| `lifecycle_hook` | live | android, cocoa, lit, nex, quart, sanic | `entrypoints.py` |
| `listener` | inert | adonisjs | _(none)_ |
| `liveview` | live | phoenix | `entrypoints.py` |
| `loader` | inert | graphql | _(none)_ |
| `logger` | inert | fuelphp, laminas | _(none)_ |
| `logging` | inert | logging-conventions | _(none)_ |
| `loop_handler` | inert | cowboy | _(none)_ |
| `mailable` | inert | adonisjs, masonite | _(none)_ |
| `mailer` | inert | cakephp, fuelphp, hanami, laminas, laravel, padrino, rails | _(none)_ |
| `main_function` | live | main-functions | `entrypoints.py` |
| `main_guard` | ghost | _(none)_ | `entrypoints.py` |
| `main_window` | inert | qt | _(none)_ |
| `marshalling` | inert | http4k | _(none)_ |
| `maven_dependency` | inert | config-conventions | _(none)_ |
| `maven_module` | inert | config-conventions | _(none)_ |
| `mcp_client` | inert | mcp | _(none)_ |
| `mcp_schema` | inert | mcp | _(none)_ |
| `mcp_server` | inert | mcp, mcp-python | _(none)_ |
| `mcp_transport` | inert | mcp | _(none)_ |
| `menu` | inert | electron | _(none)_ |
| `message_handler` | inert | micronaut | _(none)_ |
| `metadata_generator` | inert | nextjs, nuxt, remix | _(none)_ |
| `middleware` | live | adonisjs, aiohttp, akka-http, aspnet, bottle, cakephp, cli-go, codeigniter, django, express, falcon, fastapi, fastify, feathers, flask, flask-appbuilder, giraffe, go-web, grape, graphql, hanami, hapi, http4k, http4s, hummingbird, jakarta-cdi, javalin, koa, ktor, laminas, laravel, litestar, lumen, masonite, micronaut, nextjs, nuxt, openresty, padrino, pedestal, phoenix, play, plug, plumber, quart, restify, ring-compojure, rust-web, sanic, scalatra, scotty, servant, sinatra, slim, sveltekit, tornado, vapor, vertx, yii | `entrypoints.py`, `linkers/middleware_chain.py` |
| `migration` | inert | cakephp, codeigniter, fuelphp, vapor, yii | _(none)_ |
| `model` | live | adonisjs, aspnet, cakephp, codeigniter, django, fastapi, flask, fuelphp, go-web, hanami, jakarta-cdi, laminas, laravel, litestar, lumen, masonite, padrino, phoenix, plug, pyramid, qt, quarkus, quart, rails, rust-web, spring-boot, sveltekit, vapor, yesod, yii | `datamodels.py`, `linkers/orm.py` |
| `model_binding` | inert | adonisjs | _(none)_ |
| `module` | inert | fuelphp, laminas, nestjs, shiny, yii | _(none)_ |
| `mutation` | inert | remix, sveltekit | _(none)_ |
| `notification` | inert | electron, laravel | _(none)_ |
| `npm_bin` | live | config-conventions | `entrypoints.py` |
| `npm_dependency` | inert | config-conventions | _(none)_ |
| `npm_dev_dependency` | inert | config-conventions | _(none)_ |
| `npm_script` | inert | config-conventions | _(none)_ |
| `object` | inert | yii | _(none)_ |
| `option` | inert | cli, cli-go, cli-js, cli-ruby | _(none)_ |
| `output` | inert | cli, shiny | _(none)_ |
| `package` | inert | fuelphp | _(none)_ |
| `package_definition` | inert | config-conventions | _(none)_ |
| `page_handler` | inert | nex | _(none)_ |
| `paginator` | inert | laminas | _(none)_ |
| `param_matcher` | inert | http4s | _(none)_ |
| `parameter_binding` | inert | aspnet | _(none)_ |
| `params` | inert | roda | _(none)_ |
| `pass` | inert | scalatra | _(none)_ |
| `permission` | inert | django | _(none)_ |
| `pipe` | inert | nestjs | _(none)_ |
| `plugin` | inert | cakephp, hapi, laminas, nuxt, roda | _(none)_ |
| `poetry_dependency` | inert | config-conventions | _(none)_ |
| `poetry_dev_dependency` | inert | config-conventions | _(none)_ |
| `policy` | inert | laravel, rails | _(none)_ |
| `program_entrypoint` | inert | language-conventions | _(none)_ |
| `prompt_handler` | inert | mcp, mcp-python | _(none)_ |
| `property` | inert | qt | _(none)_ |
| `protocol` | inert | electron | _(none)_ |
| `protocol_handler` | inert | mcp, mcp-python | _(none)_ |
| `provider` | inert | feathers, hanami, jax-rs, laravel, symfony | _(none)_ |
| `pyproject_script` | live | config-conventions | `entrypoints.py` |
| `qml_type` | inert | qt | _(none)_ |
| `qt_class` | inert | qt | _(none)_ |
| `qualifier` | inert | guice, jakarta-cdi | _(none)_ |
| `reactive` | inert | shiny | _(none)_ |
| `reactive_property` | inert | lit | _(none)_ |
| `redirect` | inert | padrino, roda, scalatra, scotty | _(none)_ |
| `reference_target` | inert | language-conventions | _(none)_ |
| `remote` | inert | electron | _(none)_ |
| `render_method` | inert | lit | _(none)_ |
| `repository` | inert | go-web, hanami, micronaut, quarkus, rust-web, spring-boot, symfony | _(none)_ |
| `request` | inert | cowboy, fastapi, giraffe, openresty, ring-compojure, scotty, servant, slim | _(none)_ |
| `request_parser` | inert | flask-restful | _(none)_ |
| `request_type` | inert | aspnet | _(none)_ |
| `resource_handler` | inert | mcp, mcp-python | _(none)_ |
| `resource_path` | live | jax-rs | `framework_patterns.py` |
| `response` | inert | akka-http, cowboy, giraffe, openresty, pedestal, ring-compojure, roda, scotty, servant, slim | _(none)_ |
| `response_type` | inert | aspnet | _(none)_ |
| `rest_callback` | inert | cowboy | _(none)_ |
| `route` | live | adonisjs, aiohttp, akka-http, aspnet, bottle, cakephp, codeigniter, django, express, fastapi, fastify, flask, flask-appbuilder, flask-restful, giraffe, go-web, grape, graphql, hapi, http4k, hummingbird, javalin, jax-rs, koa, ktor, laravel, litestar, lumen, masonite, micronaut, nestjs, nextjs, nuxt, openresty, padrino, phoenix, play, plug, plumber, pyramid, quart, rails, restify, ring-compojure, roda, rust-web, sanic, scalatra, scotty, servant, sinatra, slim, solid, spring-boot, stapler, tornado, vapor, vertx, yesod, yii, zio | `cli.py`, `entrypoints.py`, `framework_patterns.py`, `linkers/controller_routes.py`, `linkers/http.py`, `linkers/openapi.py` |
| `route_config` | inert | remix, sveltekit | _(none)_ |
| `route_definition` | inert | roda | _(none)_ |
| `route_group` | inert | adonisjs, cakephp, codeigniter, giraffe, javalin, lumen, masonite, ring-compojure | _(none)_ |
| `route_handler` | inert | falcon, flask-restful, nex | _(none)_ |
| `route_helper` | inert | pedestal | _(none)_ |
| `route_prefix` | inert | javalin | _(none)_ |
| `route_registration` | inert | pyramid | _(none)_ |
| `route_segment` | inert | roda | _(none)_ |
| `route_terminal` | inert | roda | _(none)_ |
| `router` | inert | cowboy, giraffe, http4k, http4s, laminas, nuxt, pedestal, phoenix, plumber, remix, ring-compojure, sveltekit, vertx, yesod | _(none)_ |
| `runtime` | inert | tornado | _(none)_ |
| `scheduled` | inert | micronaut | _(none)_ |
| `scheduled_task` | live | celery, go-web, rails | `entrypoints.py` |
| `schema` | live | cli-rust, fastify | `datamodels.py` |
| `search` | inert | yii | _(none)_ |
| `security` | inert | fastapi, flask-appbuilder | _(none)_ |
| `seeder` | inert | adonisjs, cakephp, codeigniter, laravel, symfony | _(none)_ |
| `serialization_callback` | live | go-encoding-callbacks | `entrypoints.py` |
| `serializer` | inert | django, flask, grape, laravel, litestar, plumber, pyramid, quart, rails | _(none)_ |
| `serializer_field` | inert | flask-restful | _(none)_ |
| `server` | inert | cowboy, http4k, http4s, pedestal, plumber, restify, servant, shiny, vertx | _(none)_ |
| `service` | inert | codeigniter, feathers, guice, hanami, jakarta-cdi, micronaut, nestjs, spring-boot | _(none)_ |
| `service_by_name` | live | naming-conventions | `entrypoints.py` |
| `service_method` | inert | hapi | _(none)_ |
| `service_provider` | inert | adonisjs, lumen, masonite | _(none)_ |
| `servlet` | live | scalatra | `entrypoints.py` |
| `session` | inert | electron, fuelphp, laminas | _(none)_ |
| `shader_entrypoint` | inert | language-conventions | _(none)_ |
| `shared_memory` | inert | openresty | _(none)_ |
| `shell` | inert | electron | _(none)_ |
| `shortcut` | inert | electron | _(none)_ |
| `signal` | inert | qt | _(none)_ |
| `signal_handler` | inert | flask | _(none)_ |
| `slot` | inert | qt | _(none)_ |
| `socket` | inert | openresty | _(none)_ |
| `sse` | inert | pedestal | _(none)_ |
| `sse_handler` | inert | http4k, http4s, javalin, nex | _(none)_ |
| `state` | inert | nuxt, ring-compojure, solid, sveltekit | _(none)_ |
| `state_management` | inert | nex | _(none)_ |
| `static_file` | inert | akka-http | _(none)_ |
| `static_files` | inert | sanic | _(none)_ |
| `static_handler` | inert | http4k, http4s, plumber, vertx | _(none)_ |
| `strategy` | inert | laminas | _(none)_ |
| `stream_handler` | inert | cowboy | _(none)_ |
| `streaming` | inert | akka-http, play | _(none)_ |
| `stylesheet` | inert | remix | _(none)_ |
| `subrequest` | inert | openresty | _(none)_ |
| `supervisor` | inert | phoenix | _(none)_ |
| `system` | inert | electron | _(none)_ |
| `task` | live | celery, django, go-web, laravel, phoenix, quarkus, rails, rust-web, spring-boot, symfony | `entrypoints.py` |
| `telemetry` | inert | electron | _(none)_ |
| `template` | inert | fuelphp | _(none)_ |
| `template_filter` | inert | django, flask | _(none)_ |
| `template_global` | inert | flask | _(none)_ |
| `template_handler` | inert | vertx | _(none)_ |
| `template_tag` | inert | django | _(none)_ |
| `template_test` | inert | flask | _(none)_ |
| `test` | inert | aiohttp, cakephp, laminas, quarkus, tornado, yii | _(none)_ |
| `test_class` | inert | swiftui | _(none)_ |
| `test_fixture` | inert | test-frameworks | _(none)_ |
| `test_function` | live | test-frameworks | `entrypoints.py` |
| `test_lifecycle` | inert | test-frameworks | _(none)_ |
| `test_suite` | inert | test-frameworks | _(none)_ |
| `theme` | inert | electron | _(none)_ |
| `timer` | inert | openresty | _(none)_ |
| `tool_handler` | inert | mcp, mcp-python | _(none)_ |
| `transformer` | inert | servant | _(none)_ |
| `tray` | inert | electron | _(none)_ |
| `typescript_config` | inert | config-conventions | _(none)_ |
| `typescript_reference` | inert | config-conventions | _(none)_ |
| `ui_component` | inert | shiny | _(none)_ |
| `update_control` | inert | lit | _(none)_ |
| `updater` | inert | electron | _(none)_ |
| `upload` | inert | fuelphp | _(none)_ |
| `url_facet` | inert | stapler | _(none)_ |
| `validation` | inert | codeigniter, fuelphp, grape, laminas, lumen | _(none)_ |
| `validator` | inert | adonisjs, aspnet, hanami, hapi, laminas, masonite, rails, symfony, yii | _(none)_ |
| `versioning` | inert | aspnet | _(none)_ |
| `verticle` | inert | vertx | _(none)_ |
| `view` | inert | codeigniter, hanami, laminas, padrino, phoenix, qt, roda | _(none)_ |
| `view_component` | inert | cakephp, codeigniter | _(none)_ |
| `view_config` | inert | hapi | _(none)_ |
| `view_middleware` | inert | koa | _(none)_ |
| `view_model` | inert | fuelphp | _(none)_ |
| `view_registration` | inert | pyramid | _(none)_ |
| `web_service` | inert | go-web | _(none)_ |
| `websocket` | inert | http4k, http4s, javalin, pedestal, scalatra, vertx | _(none)_ |
| `websocket_callback` | inert | cowboy | _(none)_ |
| `websocket_config` | inert | javalin | _(none)_ |
| `websocket_connection` | inert | rails | _(none)_ |
| `websocket_emitter` | inert | express | _(none)_ |
| `websocket_gateway` | live | nestjs | `entrypoints.py` |
| `websocket_handler` | live | aiohttp, akka-http, cowboy, express, falcon, fastify, feathers, flask, graphql, ktor, laravel, litestar, micronaut, nestjs, phoenix, play, quart, rails, sanic, tornado, vapor | `entrypoints.py` |
| `websocket_hub` | inert | aspnet | _(none)_ |
| `websocket_middleware` | inert | javalin | _(none)_ |
| `websocket_namespace` | inert | express | _(none)_ |
| `widget` | inert | qt, yii | _(none)_ |
| `window` | inert | electron | _(none)_ |

## How to regenerate

```bash
./scripts/generate-concepts
```

The script is deterministic — given the same YAML and linker sources it produces byte-identical output. CI can re-run it and diff the result to catch drift.
