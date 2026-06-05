---

## Plano: integrar happy-dom diretamente no jsrun via polyfills de módulos Node

### Contexto

- **jsrun**: runtime V8 (deno_core) com bindings Python via PyO3. Expõe `set_module_resolver`, `set_module_loader`, `add_static_module`, `bind_function`, `register_op`.
- **happy-dom**: implementação de DOM/browser em JS puro. Depende de alguns módulos Node que não existem no jsrun.
- **Decisão arquitetural**: usar `happy-dom` diretamente (não `happy-dom-without-node`), interceptando os imports de Node via o sistema de módulos do jsrun.
- **Por que não happy-dom-without-node**: seu build destrói em bloco toda a pasta `fetch/` (incluindo `AbortController`, `AbortSignal`, `Request`, `Response`, `Headers`). Recuperar depois é mais custoso do que polyfilllar os módulos Node diretamente.

---

### Estrutura da solução

```
projeto-alvo/
  js/
    polyfills/
      node-http.js       ← transport HTTP real via op Python
      node-stream.js     ← shim mínimo de Node streams
      node-buffer.js     ← Buffer mínimo sobre Uint8Array
      node-url.js        ← reexporta globals V8
      node-crypto.js     ← reexporta globals V8
      node-net.js        ← isIP com regex
      node-vm.js         ← usa globalThis.eval
      node-console.js    ← stubs vazios
      node-fs.js         ← stub vazio (VirtualServer não é usado)
      node-path.js       ← stub vazio (VirtualServer não é usado)
      node-zlib.js       ← DecompressionStream (Web API nativa)
    bootstrap.js         ← configura process, globals e instancia BrowserWindow
  runtime_setup.py       ← monta o jsrun Runtime com tudo wired
```

---

### Passo 1 — Instalar happy-dom no projeto

No projeto alvo (onde o JS roda dentro do jsrun), adicionar `happy-dom` como dependência e compilar/disponibilizar seus arquivos `lib/`. O jsrun não usa npm diretamente — os módulos são servidos via loader Python.

A forma mais simples é instalar happy-dom num `package.json` auxiliar e apontar o loader para `node_modules/happy-dom/lib/`.

```bash
npm install happy-dom
```

O loader Python vai ler os `.js` do `node_modules/happy-dom/lib/` em disco e servir para o jsrun.

---

### Passo 2 — Criar o module resolver e loader Python

```python
import pathlib

HAPPY_DOM_LIB = pathlib.Path("node_modules/happy-dom/lib")

NODE_MODULE_MAP = {
    "http":       "polyfills/node-http.js",
    "https":      "polyfills/node-http.js",   # mesmo polyfill
    "fs":         "polyfills/node-fs.js",
    "path":       "polyfills/node-path.js",
    "url":        "polyfills/node-url.js",
    "stream":     "polyfills/node-stream.js",
    "stream/web": "polyfills/node-stream-web.js",
    "buffer":     "polyfills/node-buffer.js",
    "crypto":     "polyfills/node-crypto.js",
    "net":        "polyfills/node-net.js",
    "vm":         "polyfills/node-vm.js",
    "console":    "polyfills/node-console.js",
    "zlib":       "polyfills/node-zlib.js",
    "child_process": "polyfills/node-child-process.js",  # SyncFetch — stub
}

POLYFILLS_DIR = pathlib.Path("js/polyfills")

def module_resolver(specifier: str, referrer: str) -> str | None:
    # Módulos Node conhecidos
    if specifier in NODE_MODULE_MAP:
        return f"node-polyfill:{specifier}"
    # Módulos do happy-dom (imports relativos já resolvem; imports absolutos não)
    if referrer.startswith("happy-dom:") and specifier.startswith("."):
        base = pathlib.Path(referrer.removeprefix("happy-dom:")).parent
        resolved = (base / specifier).resolve().relative_to(HAPPY_DOM_LIB.resolve())
        return f"happy-dom:{resolved}"
    if specifier == "happy-dom":
        return "happy-dom:index.js"
    return None

async def module_loader(specifier: str) -> str:
    if specifier.startswith("node-polyfill:"):
        mod = specifier.removeprefix("node-polyfill:")
        path = POLYFILLS_DIR / NODE_MODULE_MAP[mod]
        return path.read_text()
    if specifier.startswith("happy-dom:"):
        rel = specifier.removeprefix("happy-dom:")
        return (HAPPY_DOM_LIB / rel).read_text()
    raise ValueError(f"Unknown module: {specifier}")
```

---

### Passo 3 — Polyfills por módulo

#### `node-url.js` (trivial)
```js
export const URL = globalThis.URL;
export const URLSearchParams = globalThis.URLSearchParams;
export default { URL, URLSearchParams };
```

#### `node-crypto.js` (trivial)
```js
const webcrypto = globalThis.crypto;
export { webcrypto };
export default { webcrypto };
```

#### `node-net.js` (copiar do happy-dom-without-node)
```js
function isIP(ip) {
    const ipv4 = /^(25[0-5]|2[0-4]\d|[01]?\d\d?)(\.(25[0-5]|2[0-4]\d|[01]?\d\d?)){3}$/;
    const ipv6 = /^([0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}$/; // simplificado
    if (ipv4.test(ip)) return 4;
    if (ipv6.test(ip)) return 6;
    return 0;
}
export { isIP };
export default { isIP };
```

#### `node-vm.js` (copiar do happy-dom-without-node)
```js
class Script {
    constructor(code) { this.code = code; }
    runInContext(context) {
        const evaluate = (code) => { globalThis.eval(code); };
        evaluate.call(context, this.code);
    }
}
const contextSymbol = Symbol('context');
const isContext = (ctx) => ctx[contextSymbol] === true;
const createContext = (ctx) => { ctx[contextSymbol] = true; return ctx; };
export { Script, isContext, createContext };
export default { Script, isContext, createContext };
```

#### `node-console.js` (stub)
```js
class ConsoleConstructor {}
class Console {}
export { Console, ConsoleConstructor };
```

#### `node-fs.js` e `node-path.js` (stubs — VirtualServer não será usado)
```js
// node-fs.js
export default {};

// node-path.js
export default {};
```

#### `node-child-process.js` (stub — SyncFetch não é usado)
```js
export default {};
```

#### `node-stream-web.js` (trivial)
```js
export const ReadableStream = globalThis.ReadableStream;
export default { ReadableStream };
```

#### `node-buffer.js` (implementação mínima)

O happy-dom usa: `Buffer.from()`, `Buffer.isBuffer()`, `Buffer.concat()`, `Buffer.alloc()`, instâncias como `Uint8Array` com método `.toString('utf8'/'base64'/'hex')`.

```js
class Buffer extends Uint8Array {
    static from(value, encodingOrOffset, length) {
        if (typeof value === 'string') {
            const enc = encodingOrOffset || 'utf8';
            if (enc === 'utf8' || enc === 'utf-8') {
                return new Buffer(new TextEncoder().encode(value).buffer);
            }
            if (enc === 'base64') {
                const bin = atob(value);
                const bytes = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                return new Buffer(bytes.buffer);
            }
            if (enc === 'hex') {
                const bytes = new Uint8Array(value.length / 2);
                for (let i = 0; i < bytes.length; i++)
                    bytes[i] = parseInt(value.slice(i * 2, i * 2 + 2), 16);
                return new Buffer(bytes.buffer);
            }
        }
        if (value instanceof ArrayBuffer) return new Buffer(value);
        if (ArrayBuffer.isView(value)) return new Buffer(value.buffer, value.byteOffset, value.byteLength);
        if (Array.isArray(value)) return new Buffer(new Uint8Array(value).buffer);
        return new Buffer(value);
    }
    static isBuffer(obj) { return obj instanceof Buffer; }
    static alloc(size, fill = 0) {
        const b = new Buffer(size);
        b.fill(fill);
        return b;
    }
    static concat(buffers, totalLength) {
        const len = totalLength ?? buffers.reduce((s, b) => s + b.length, 0);
        const result = new Buffer(len);
        let offset = 0;
        for (const b of buffers) { result.set(b, offset); offset += b.length; }
        return result;
    }
    toString(encoding = 'utf8') {
        if (encoding === 'utf8' || encoding === 'utf-8')
            return new TextDecoder().decode(this);
        if (encoding === 'base64')
            return btoa(String.fromCharCode(...this));
        if (encoding === 'hex')
            return Array.from(this).map(b => b.toString(16).padStart(2, '0')).join('');
        return new TextDecoder().decode(this);
    }
}
export { Buffer };
export default { Buffer };
```

#### `node-zlib.js`

O happy-dom usa: `Zlib.createGunzip()`, `Zlib.createInflate()`, `Zlib.createInflateRaw()`, `Zlib.createBrotliDecompress()`, `Zlib.constants.Z_SYNC_FLUSH`.

Usa-os via `Stream.pipeline(nodeResponse, Zlib.createGunzip(), cb)`. Com a abordagem de transport próprio (passo 4), o transporte já pode retornar o body descomprimido — zerando a necessidade do zlib. Se precisar, usar `DecompressionStream`:

```js
// Stub que não faz nada se o transport já descomprime.
// Se precisar funcional: implementar via TransformStream + DecompressionStream
const constants = { Z_SYNC_FLUSH: 2, Z_FINISH: 4 };
const noop = () => ({ pipe: (s) => s, on: () => {} });
export default {
    constants,
    createGunzip: noop,
    createInflate: noop,
    createInflateRaw: noop,
    createBrotliDecompress: noop,
};
```

**Atenção**: se o servidor retornar respostas comprimidas e o transport Python não descomprimir automaticamente, este stub vai corromper o body. Nesse caso, implementar via `DecompressionStream` ou garantir que o transport Python use `httpx` com `follow_redirects=True` e descompressão automática (padrão no httpx).

#### `node-stream.js` (o mais complexo fora do fetch)

O happy-dom usa: `Stream.pipeline()`, `Stream.PassThrough`, e `FetchBodyUtility.nodeToWebStream()`. Com transport próprio no passo 4, o caminho de `nodeToWebStream` nunca é chamado (só é chamado quando a response vem de `HTTP.request`). Restam `pipeline` e `PassThrough` usados em `cloneBodyStream`:

```js
class PassThrough {
    constructor() {
        this._chunks = [];
        this._listeners = {};
        this._ended = false;
    }
    on(event, fn) {
        this._listeners[event] = this._listeners[event] || [];
        this._listeners[event].push(fn);
        return this;
    }
    emit(event, ...args) {
        (this._listeners[event] || []).forEach(fn => fn(...args));
    }
    write(chunk) { this._chunks.push(chunk); this.emit('data', chunk); }
    end() { this._ended = true; this.emit('end'); }
    pipe(dest) { /* passthrough simples */ return dest; }
}

function pipeline(...args) {
    // Stub mínimo — com transport próprio este caminho não é chamado
    const cb = args[args.length - 1];
    if (typeof cb === 'function') cb(null);
}

export { PassThrough, pipeline };
export default { PassThrough, pipeline };
```

---

### Passo 4 — Transport HTTP via op Python

Este é o único polyfill não-trivial. O happy-dom chama `HTTP.request(options, callback)` onde `callback` recebe um `IncomingMessage` (stream de response).

A abordagem: substituir `Fetch.ts` inteiro por uma implementação que usa op Python. Há duas sub-opções:

**Sub-opção A (recomendada)**: não usar `Fetch.ts` do happy-dom. Após instanciar o `BrowserWindow`, substituir `window.fetch` por uma implementação própria em JS que chama op Python:

```js
// No bootstrap.js, após instanciar BrowserWindow:
window.fetch = async function(input, init) {
    const request = new window.Request(input, init);
    const url = request.url;
    const method = request.method;
    const headers = Object.fromEntries(request.headers.entries());
    const body = request.body ? await request.arrayBuffer() : null;

    // __host_op_async__ chama Python
    const result = await __host_op_async__(FETCH_OP_ID, { url, method, headers, body });

    return new window.Response(result.body, {
        status: result.status,
        statusText: result.statusText,
        headers: result.headers,
    });
};
```

No Python, registrar o op de fetch:
```python
import httpx

async def python_fetch(args):
    req = args[0]
    async with httpx.AsyncClient() as client:
        r = await client.request(
            req["method"],
            req["url"],
            headers=req.get("headers", {}),
            content=req.get("body"),
        )
        return {
            "status": r.status_code,
            "statusText": "",
            "headers": dict(r.headers),
            "body": r.content,  # bytes → Uint8Array no jsrun
        }

fetch_op_id = runtime.register_op("fetch", python_fetch, mode="async")
```

**Sub-opção B**: manter `Fetch.ts` do happy-dom e fazer `node-http.js` imitar a API de `http.request` chamando Python. Mais fiel ao happy-dom mas mais trabalhosa de implementar corretamente (precisa emular `ClientRequest`, `IncomingMessage`, events, etc.).

Recomenda-se a Sub-opção A: mais simples, mais controlável, desacopla transport de implementação DOM.

---

### Passo 5 — Bootstrap: process global e instância do BrowserWindow

O happy-dom acessa `process.platform` e `process.arch` fora do fetch também (em detecção de ambiente). Adicionar no bootstrap antes de carregar o happy-dom:

```js
// bootstrap.js
globalThis.process = {
    platform: 'browser',
    arch: 'unknown',
    env: {},
    version: '',
    versions: {},
};

// setImmediate — deno_core pode já ter, mas garantir:
if (!globalThis.setImmediate) {
    globalThis.setImmediate = (fn, ...args) => setTimeout(fn, 0, ...args);
    globalThis.clearImmediate = clearTimeout;
}
```

Depois importar e instanciar o BrowserWindow:

```js
// ainda no bootstrap.js (ou num módulo separado)
import { Window } from 'happy-dom';

const window = new Window({
    url: 'https://localhost/',
    width: 1024,
    height: 768,
});

// Expor no globalThis para que o código do usuário acesse
globalThis.window = window;
globalThis.document = window.document;
globalThis.AbortController = window.AbortController;
globalThis.AbortSignal = window.AbortSignal;
globalThis.Headers = window.Headers;
globalThis.Request = window.Request;
globalThis.Response = window.Response;
// (fetch será sobrescrito pelo passo 4)
```

---

### Passo 6 — Montagem final em Python

```python
import pathlib
from jsrun import Runtime

async def create_dom_runtime(fetch_handler=None) -> Runtime:
    runtime = Runtime()

    # Registrar op de fetch
    if fetch_handler is None:
        fetch_handler = default_python_fetch  # implementação do passo 4
    fetch_op_id = runtime.register_op("fetch", fetch_handler, mode="async")

    # Injetar FETCH_OP_ID no ambiente JS antes do bootstrap
    runtime.eval(f"globalThis.__FETCH_OP_ID__ = {fetch_op_id};")

    # Wiring de módulos
    runtime.set_module_resolver(module_resolver)
    runtime.set_module_loader(module_loader)

    # Carregar bootstrap (que importa happy-dom e configura o DOM)
    await runtime.eval_module_async("bootstrap")

    return runtime
```

---

### Ordem de implementação recomendada

1. **Resolver + loader** básico (passo 2) com polyfills triviais (`url`, `crypto`, `net`, `vm`, `console`, `fs`, `path`)
2. **Bootstrap** com `process` global (passo 5, sem happy-dom ainda) — verificar que o runtime inicia
3. **Carregar happy-dom** e ver quais erros de módulo aparecem — adicionar polyfills sob demanda
4. **`node-buffer.js`** — provavelmente o primeiro erro real fora do fetch
5. **`node-stream.js`** stub mínimo
6. **`node-http.js`** stub vazio primeiro para o happy-dom carregar; depois sub-opção A do passo 4 para fetch funcionar
7. **`node-zlib.js`** — stub se o transport Python descomprime; funcional se não

### Sinais de sucesso por etapa

- Etapa 3: `new window.Document()` funciona sem erro
- Etapa 5: `window.AbortController` é instanciável e `signal.aborted` funciona
- Etapa 6: `await window.fetch('https://...')` retorna `Response` com `.json()` funcional
- Final: `new window.DOMParser().parseFromString('<p>ok</p>', 'text/html')` retorna documento navegável
