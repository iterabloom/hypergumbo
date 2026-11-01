# SPDX-License-Identifier: AGPL-3.0-or-later
"""IPC wrapper sites (WI-pumit): a call through a same-repo IPC wrapper is a site.

A main process that registers its handlers through its own wrapper
(podman-desktop ``ipcHandle(channel, listener) { ipcMain.handle(channel, ...) }``
called as ``this.ipcHandle('containerfile:getInfo', ...)``) used to yield no
receive site, so the renderer -> preload -> main chain stopped at the preload.
The invariant asserted here: a function or method whose body forwards its
channel PARAMETER to an Electron IPC call is a wrapper, and a call to it that
can see it, with a literal (or same-file constant) channel, is an IPC site of
the forwarded call's direction at the call's line. The forwarding call inside
the wrapper names a parameter, not a channel, so it is no site of its own.
"""
from pathlib import Path

from hypergumbo_core.linkers.ipc import link_ipc


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _reaches(edges: list, src_pred, dst_pred) -> bool:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.src, set()).add(edge.dst)
    frontier = [e.src for e in edges if src_pred(e.src)]
    seen = set(frontier)
    while frontier:
        node = frontier.pop()
        if dst_pred(node):
            return True
        for nxt in adjacency.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return False


def _sites(result, role: str) -> list:
    return [
        s for s in result.symbols
        if (s.meta or {}).get("framework_role") == role
    ]


def _site_keys(result, role: str) -> set[tuple[str, int, str]]:
    """(file name, line, channel) of every site symbol with ``role``."""
    return {
        (Path(s.path).name, s.span.start_line, s.meta["channel"])
        for s in _sites(result, role)
    }


_PRELOAD = (
    "import { contextBridge, ipcRenderer } from 'electron';\n"
    "async function ipcInvoke<T>(channel: string, ...args: unknown[]): Promise<T> {\n"
    "  const { error, result } = await ipcRenderer.invoke(channel, ...args);\n"
    "  if (error) { throw new Error('}'); }\n"
    "  return result;\n"
    "}\n"
    "contextBridge.exposeInMainWorld('getInfo', async (path: string) => {\n"
    "  return ipcInvoke('containerfile:getInfo', path);\n"
    "});\n"
)
_RENDERER = "export async function load(p: string) {\n  return window.getInfo(p);\n}\n"
_MAIN = (
    "import { ipcMain } from 'electron';\n"
    "export class PluginSystem {\n"
    "  // a brace in a comment: {\n"
    "  ipcHandle(channel: string, listener: (event: unknown, ...args: any[]) => Promise<void> | any): void {\n"
    "    ipcMain.handle(channel, async (...args) => {\n"
    "      return { result: await Promise.resolve(listener(...args)) };\n"
    "    });\n"
    "  }\n"
    "  init(): void {\n"
    "    this.ipcHandle(\n"
    "      'containerfile:getInfo', async (_l, path: string) => path);\n"
    "  }\n"
    "}\n"
    "export const pluginSystem = new PluginSystem();\n"
)


class TestReceiveWrapperConnectsTheChain:
    def test_renderer_reaches_main_through_this_ipc_handle(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "preload/index.ts": _PRELOAD,
            "renderer/app.ts": _RENDERER,
            "main/index.ts": _MAIN,
        })
        result = link_ipc(tmp_path)

        # Reach: the bridge edge exists, so the fixture exercises the chain.
        assert any(e.edge_type == "calls" for e in result.edges)
        # The wrapper call is the receive site, at the call's (name token) line.
        assert _site_keys(result, "ipc_receive") == {
            ("index.ts", 10, "containerfile:getInfo"),
        }
        assert _reaches(
            result.edges,
            lambda n: n.endswith(":bridge_caller") and "app.ts" in n,
            lambda n: "main/index.ts" in n and ":receive:containerfile:getInfo" in n,
        ), "renderer -> preload -> main path is disconnected"

    def test_forwarding_call_is_no_site(self, tmp_path: Path) -> None:
        """``ipcMain.handle(channel, ...)`` inside the wrapper names a parameter:
        no site, so no identifier join between two wrappers' parameters."""
        _write(tmp_path, {
            "preload/index.ts": _PRELOAD,
            "renderer/app.ts": _RENDERER,
            "main/index.ts": _MAIN,
        })
        result = link_ipc(tmp_path)

        assert not [e for e in result.edges if e.evidence_type == "variable_match"]
        assert all(s.meta.get("channel_identifier") != "channel" for s in result.symbols)
        # One send node for the bridged wrapper call (not one per detector).
        assert _site_keys(result, "ipc_send") == {
            ("index.ts", 8, "containerfile:getInfo"),
        }

    def test_injected_member_and_instance_calls_are_sites(self, tmp_path: Path) -> None:
        """podman's DI shape (``private readonly ipcHandle: IPCHandle``) and
        vscode's instance shape (``validatedIpcMain.handle('ch')``)."""
        _write(tmp_path, {
            "main/index.ts": _MAIN,
            "main/parser.ts": (
                "export class ContainerfileParser {\n"
                "  constructor(@inject(IPCHandle) private readonly ipcHandle: IPCHandle) {}\n"
                "  init(): void {\n"
                "    this.ipcHandle('parser:parse', async () => 1);\n"
                "  }\n"
                "}\n"
            ),
            "main/ipcMain.ts": (
                "import electron from 'electron';\n"
                "class ValidatedIpcMain {\n"
                "  on(channel: string, listener: Function): this {\n"
                "    const wrapped = (event, ...args) => listener(event, ...args);\n"
                "    electron.ipcMain.on(channel, wrapped);\n"
                "    return this;\n"
                "  }\n"
                "  once(channel: string, listener: Function): this {\n"
                "    electron.ipcMain.once(channel, listener);\n"
                "    return this;\n"
                "  }\n"
                "}\n"
                "export const validatedIpcMain = new ValidatedIpcMain();\n"
            ),
            "main/app.ts": (
                "import { validatedIpcMain } from './ipcMain';\n"
                "const TOGGLE = 'vscode:toggleDevTools';\n"
                "validatedIpcMain.on(TOGGLE, e => e.sender.toggleDevTools());\n"
                "validatedIpcMain.once('vscode:ready', () => {});\n"
                "pluginSystem.ipcHandle('plugin:viaInstance', () => 1);\n"
            ),
        })
        result = link_ipc(tmp_path)

        assert _site_keys(result, "ipc_receive") == set(), "no sender: no receive node"
        # Receive symbols are emitted only when a sender joins: add one per site.
        _write(tmp_path, {"preload/p.ts": (
            "ipcRenderer.invoke('containerfile:getInfo');\n"
            "ipcRenderer.invoke('parser:parse');\n"
            "ipcRenderer.send('vscode:toggleDevTools');\n"
            "ipcRenderer.send('vscode:ready');\n"
            "ipcRenderer.invoke('plugin:viaInstance');\n"
        )})
        result = link_ipc(tmp_path)
        assert _site_keys(result, "ipc_receive") == {
            ("index.ts", 10, "containerfile:getInfo"),
            ("parser.ts", 4, "parser:parse"),
            ("app.ts", 3, "vscode:toggleDevTools"),
            ("app.ts", 4, "vscode:ready"),
            ("app.ts", 5, "plugin:viaInstance"),
        }
        constant = [
            s for s in _sites(result, "ipc_receive")
            if s.meta.get("channel_identifier") == "TOGGLE"
        ]
        assert [s.meta["channel_type"] for s in constant] == ["constant"]


class TestWrapperForms:
    def test_send_wrappers_function_arrow_and_expression(self, tmp_path: Path) -> None:
        """Send wrappers outside any contextBridge exposure: a function, an
        expression-bodied arrow (no semicolons) and a function expression."""
        _write(tmp_path, {
            "renderer/ipc.js": (
                "export const ipcSend = (channel, ...a) =>\n"
                "  ipcRenderer.send(channel, ...a)\n"
                "export const notify = (channel) => console.log(channel)\n"
                "const ask = function (ch, x) { return ipcRenderer.invoke(ch, x); };\n"
                "ask('ask:me', 1);\n"
            ),
            "renderer/view.js": (
                "import { ipcSend, notify } from './ipc';\n"
                "ipcSend('win:close');\n"
                "notify('win:minimize');\n"
                "ask('ask:me', 2);\n"
            ),
            "main/main.js": (
                "ipcMain.on('win:close', () => {});\n"
                "ipcMain.on('win:minimize', () => {});\n"
                "ipcMain.handle('ask:me', () => {});\n"
            ),
        })
        result = link_ipc(tmp_path)

        assert _site_keys(result, "ipc_send") == {
            ("view.js", 2, "win:close"),
            ("ipc.js", 5, "ask:me"),
        }
        # ``notify`` forwards to console.log, so it is no wrapper; ``ask`` is
        # neither imported by nor defined in view.js, so view.js cannot see it.
        published = {(e.line, e.meta["channel"]) for e in result.edges
                     if e.edge_type == "event_publishes"}
        assert published == {(2, "win:close"), (5, "ask:me")}

    def test_renderer_receive_wrapper_joins_webcontents_send(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "preload/events.ts": (
                "function onChannel(channel: string, cb: (...a: unknown[]) => void) {\n"
                "  ipcRenderer.on(channel, (_e, ...a) => cb(...a));\n"
                "}\n"
                "onChannel('theme:changed', t => apply(t));\n"
            ),
            "main/win.ts": "win.webContents.send('theme:changed', 'dark');\n",
        })
        result = link_ipc(tmp_path)

        assert _site_keys(result, "ipc_receive") == {("events.ts", 4, "theme:changed")}
        assert [e.line for e in result.edges if e.edge_type == "event_publishes"] == [1]


_SENDER = {"preload/p.ts": "ipcRenderer.invoke('ch:x');\nipcRenderer.invoke('ch:y');\n"}


class TestWrapperFalsePositiveGuards:

    def test_same_named_function_that_does_not_forward_is_no_wrapper(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, {**_SENDER, "main/registry.ts": (
            "class Registry {\n"
            "  ipcHandle(channel: string, fn: Function): void {\n"
            "    this.handlers.set(channel, fn);\n"
            "  }\n"
            "  init() { this.ipcHandle('ch:x', () => 1); }\n"
            "}\n"
        )})
        result = link_ipc(tmp_path)
        assert _site_keys(result, "ipc_receive") == set()
        assert not [e for e in result.edges if e.edge_type == "event_publishes"]

    def test_wrapper_that_forwards_a_literal_is_no_wrapper(self, tmp_path: Path) -> None:
        _write(tmp_path, {**_SENDER, "main/m.ts": (
            "class M {\n"
            "  ipcHandle(channel: string, fn: Function): void {\n"
            "    ipcMain.handle('ch:y', fn);\n"
            "  }\n"
            "  init() { this.ipcHandle('ch:x', () => 1); }\n"
            "}\n"
        )})
        result = link_ipc(tmp_path)
        # The literal forward is an ordinary site; the call is none.
        assert _site_keys(result, "ipc_receive") == {("m.ts", 3, "ch:y")}

    def test_wrapper_called_with_non_literal_channel_is_no_site(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, {**_SENDER, "main/m.ts": (
            "class M {\n"
            "  ipcHandle(channel: string, fn: Function): void {\n"
            "    ipcMain.handle(channel, fn);\n"
            "  }\n"
            "  init(ch: string) {\n"
            "    this.ipcHandle(ch, () => 1);\n"
            "    this.ipcHandle(config.channel, () => 1);\n"
            "    this.ipcHandle(`ch:${x}`, () => 1);\n"
            "  }\n"
            "}\n"
        )})
        result = link_ipc(tmp_path)
        assert _site_keys(result, "ipc_receive") == set()
        assert not [e for e in result.edges if e.edge_type == "event_publishes"]

    def test_calls_that_cannot_see_the_wrapper_are_no_sites(self, tmp_path: Path) -> None:
        """A bare call in a file that neither defines nor imports the wrapper,
        ``this.x()`` in a class that declares no ``x`` member, ``obj.x()`` on an
        object that is no instance of the wrapper's class, and a call in a
        comment are not sites."""
        _write(tmp_path, {**_SENDER, "main/w.ts": (
            "export function handleIt(channel: string, fn: Function) {\n"
            "  ipcMain.handle(channel, fn);\n"
            "}\n"
            "export class W {\n"
            "  on(channel: string, fn: Function) { ipcMain.on(channel, fn); }\n"
            "}\n"
            "const obj = { reg(channel, fn) { ipcMain.handle(channel, fn); } };\n"
            "obj.reg('ch:x', f);\n"
            "// handleIt('ch:x', f);\n"
        ), "main/other.ts": (
            "function handleIt(channel: string, fn: Function) { log(channel); }\n"
            "handleIt('ch:x', f);\n"
            "class Emitter extends EventEmitter {\n"
            "  start() { this.on('ch:x', f); }\n"
            "}\n"
            "emitter.on('ch:y', f);\n"
            "socket.handleIt('ch:y', f);\n"
        )})
        result = link_ipc(tmp_path)
        assert _site_keys(result, "ipc_receive") == set()
        assert not [e for e in result.edges if e.edge_type == "event_publishes"]


class TestIpcMainOnce:
    def test_ipc_main_once_is_a_receive_site(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "r.js": "ipcRenderer.send('app:ready');\n",
            "m.js": "ipcMain.once('app:ready', () => {});\n",
        })
        result = link_ipc(tmp_path)
        assert _site_keys(result, "ipc_receive") == {("m.js", 1, "app:ready")}


class TestWrapperEdgeShapes:
    def test_bare_param_arrow_and_object_method_forwards(self, tmp_path: Path) -> None:
        """``const f = channel => ...`` is a wrapper; an object-literal method
        forwarding its parameter (vscode's preload shape) is no site itself,
        and no call form resolves to it."""
        _write(tmp_path, {
            "preload/p.js": (
                "const ipcSend = channel =>\n"
                "  ipcRenderer.send(channel)\n"
                "ipcSend('a:b');\n"
                "const api = {\n"
                "  send(channel, ...args) {\n"
                "    if (ok(channel)) { ipcRenderer.send(channel, ...args); }\n"
                "  },\n"
                "};\n"
                "api.send('a:c');\n"
            ),
            "main/m.js": (
                "ipcMain.on('a:b', () => {});\n"
                "ipcMain.on('a:c', () => {});\n"
                "ipcMain.on(channel, () => {});\n"
            ),
        })
        result = link_ipc(tmp_path)
        assert _site_keys(result, "ipc_send") == {("p.js", 3, "a:b")}
        assert {e.line for e in result.edges} == {3}

    def test_two_visible_same_named_wrappers_give_one_site(self, tmp_path: Path) -> None:
        """podman has two ``ipcHandle`` methods; an injected call sees both."""
        wrapper = (
            "export class {cls} {{\n"
            "  ipcHandle(channel: string, l: Function): void {{\n"
            "    ipcMain.handle(channel, l);\n"
            "  }}\n"
            "}}\n"
        )
        _write(tmp_path, {
            **_SENDER,
            "main/a.ts": wrapper.format(cls="A"),
            "main/b.ts": wrapper.format(cls="B"),
            "main/c.ts": (
                "class C {\n"
                "  constructor(private ipcHandle: IPCHandle) {}\n"
                "  init() { this.ipcHandle('ch:x', () => 1); }\n"
                "}\n"
            ),
        })
        result = link_ipc(tmp_path)
        assert _site_keys(result, "ipc_receive") == {("c.ts", 3, "ch:x")}
        assert len([e for e in result.edges if e.edge_type == "event_publishes"]) == 1

    def test_undefined_bridge_wrapper_still_publishes(self, tmp_path: Path) -> None:
        """A bridge ``ipcInvoke('ch')`` whose definition is not in the repo is
        no detected wrapper; the bridge's own send site still joins main."""
        _write(tmp_path, {
            "preload.ts": (
                "import { ipcInvoke } from 'some-ipc-lib';\n"
                "contextBridge.exposeInMainWorld('ping', async () => {\n"
                "  return ipcInvoke('app:ping');\n"
                "});\n"
            ),
            "renderer.ts": "window.ping();\n",
            "main.ts": "ipcMain.handle('app:ping', () => 1);\n",
        })
        result = link_ipc(tmp_path)
        assert _site_keys(result, "ipc_send") == {("preload.ts", 3, "app:ping")}
        assert _site_keys(result, "ipc_receive") == {("main.ts", 1, "app:ping")}


class TestScanHelpers:
    def test_mask_keeps_offsets_and_blanks_strings_and_comments(self) -> None:
        from hypergumbo_core.linkers.ipc import _mask_js

        text = "a('{', \"}\", `${x}`); // {\n/* } */ b('\\'{')\n'unterminated {\nc"
        masked = _mask_js(text)
        assert len(masked) == len(text)
        assert masked.count("\n") == text.count("\n")
        assert "{" not in masked and "}" not in masked
        assert masked.endswith("\nc")

    def test_unclosed_scans_run_to_the_end(self) -> None:
        from hypergumbo_core.linkers.ipc import _close_of, _expression_end

        assert _close_of("f(a, (b)", 1) == len("f(a, (b)")
        assert _expression_end("send(x)", 0) == len("send(x)")
        assert _expression_end("send(x)\n.then(y); z", 0) == len("send(x)\n.then(y)")
