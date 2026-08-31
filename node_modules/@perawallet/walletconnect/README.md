# @perawallet/walletconnect

A modern, security-forward implementation of the **WalletConnect v1** client
protocol, maintained by [Pera Wallet](https://perawallet.app). It provides both
sides of the protocol — the **wallet** API (receive session and call requests,
approve or reject them) and the **dapp** API (create sessions, send requests) —
with full wire compatibility with the original `@walletconnect/client@1.8.0`.

## Why this exists

- **WC v1 is still in production.** Pera's infrastructure and ecosystem
  integrations are pinned to the WalletConnect v1 protocol, which speaks a
  simple, self-hostable bridge protocol.
- **Upstream is abandoned.** The original
  [walletconnect-monorepo](https://github.com/WalletConnect/walletconnect-monorepo)
  v1.x line is unmaintained, with stale dependencies and no security fixes.
- **WC v2+ is a walled garden.** ReOwn's v2+ protocol requires their hosted
  relay and project registration; v1 can run entirely on infrastructure you
  control.
- **Supply-chain control.** The legacy client pulled in a long tail of
  transitive dependencies. This package re-implements everything on top of
  exactly two audited cryptography libraries and nothing else.

## Install

```sh
pnpm add @perawallet/walletconnect
# or
npm install @perawallet/walletconnect
# or
yarn add @perawallet/walletconnect
```

Runtime dependencies: [`@noble/ciphers`](https://github.com/paulmillr/noble-ciphers)
and [`@noble/hashes`](https://github.com/paulmillr/noble-hashes) — that's all.

**Supported environments:** Node.js ≥ 22 (uses the native `WebSocket` global),
modern browsers, and React Native (see [React Native](#react-native) below).
Ships as dual ESM + CJS with full TypeScript types.

## Usage

### Wallet side

A wallet joins an existing session from a `wc:` URI (scanned from a QR code or
opened via deep link), then responds to requests from the dapp:

```ts
import WalletConnect from "@perawallet/walletconnect";

const connector = new WalletConnect({
  uri, // "wc:..." URI from the dapp's QR code / deep link
  clientMeta: {
    name: "Pera Wallet",
    description: "Algorand wallet",
    url: "https://perawallet.app",
    icons: ["https://perawallet.app/icon.png"],
  },
  // Required in React Native, optional elsewhere (defaults to localStorage):
  // storage: <ISessionStorage adapter, see "React Native" below>
});

// The dapp asks to connect
connector.on("session_request", (error, payload) => {
  if (error) throw error;
  const { peerMeta } = payload.params[0];
  // ...show approval UI for peerMeta...
  connector.approveSession({
    accounts: ["ALGORANDADDRESS..."],
    chainId: 4160,
  });
  // or: connector.rejectSession({ message: "User rejected" });
});

// The dapp sends a JSON-RPC call (e.g. a signing request)
connector.on("call_request", (error, payload) => {
  if (error) throw error;
  if (payload.method === "algo_signTxn") {
    // ...sign payload.params...
    connector.approveRequest({ id: payload.id, result: [signedTxn] });
    // or: connector.rejectRequest({ id: payload.id, error: { message: "Rejected" } });
  }
});

connector.on("disconnect", error => {
  // session ended by the peer
});

// End the session from the wallet side
await connector.killSession();
```

### Dapp side

A dapp creates a session on a bridge and displays the pairing URI to the user:

```ts
import WalletConnect from "@perawallet/walletconnect";

const connector = new WalletConnect({
  bridge: "https://your-bridge.example.com", // your self-hosted bridge server
  clientMeta: {
    name: "My Dapp",
    description: "Example dapp",
    url: "https://dapp.example.com",
    icons: ["https://dapp.example.com/icon.png"],
  },
  qrcodeModal: {
    open: (uri, cb) => showQrCode(uri), // render the wc: URI as a QR code
    close: () => hideQrCode(),
  },
});

await connector.createSession({ chainId: 4160 });
// connector.uri now holds the "wc:..." pairing URI

connector.on("connect", (error, payload) => {
  if (error) throw error;
  const { accounts } = payload.params[0]; // wallet approved
});

connector.on("session_update", (error, payload) => {
  if (error) throw error;
  const { accounts } = payload.params[0]; // wallet changed accounts/chain
});

// Send a custom JSON-RPC request to the wallet
const signed = await connector.sendCustomRequest(
  { method: "algo_signTxn", params: [[{ txn: base64Txn }]] },
  { forcePushNotification: true },
);

await connector.killSession();
```

> **Note on bridges:** the original public `*.bridge.walletconnect.org` servers
> have been shut down by ReOwn. Wallet-side connections take the bridge from
> the `wc:` URI automatically; dapp-side code should pass the URL of a bridge
> server you operate via the `bridge` option.

### React Native

Two requirements, both enforced with clear errors:

1. **`crypto.getRandomValues` must exist globally _before_ constructing
   `WalletConnect`.** Install and import a polyfill such as
   [`react-native-quick-crypto`](https://github.com/margelo/react-native-quick-crypto)
   or [`react-native-get-random-values`](https://github.com/LinusU/react-native-get-random-values)
   at the top of your app's entry point. The constructor throws if it is
   missing.

2. **Inject a storage adapter.** React Native has no `localStorage`, so pass a
   synchronous adapter via the `storage` option to persist sessions. With
   [MMKV](https://github.com/mrousavy/react-native-mmkv) (synchronous, ideal):

```ts
import { MMKV } from "react-native-mmkv";
import WalletConnect, { type IWalletConnectSession } from "@perawallet/walletconnect";

const mmkv = new MMKV();
const STORAGE_KEY = "walletconnect";

const storage = {
  getSession: (): IWalletConnectSession | null => {
    const json = mmkv.getString(STORAGE_KEY);
    return json ? JSON.parse(json) : null;
  },
  setSession: (session: IWalletConnectSession) => {
    mmkv.set(STORAGE_KEY, JSON.stringify(session));
    return session;
  },
  removeSession: () => {
    mmkv.delete(STORAGE_KEY);
  },
};

const connector = new WalletConnect({ uri, clientMeta, storage });
```

The adapter interface is synchronous (`getSession` / `setSession` /
`removeSession`). If you use AsyncStorage, hydrate the session into memory at
app startup and back the adapter with that in-memory copy.

## Security posture

- **Two runtime dependencies, both audited crypto primitives:**
  `@noble/ciphers` (AES-CBC) and `@noble/hashes` (HMAC-SHA256, SHA-512,
  Keccak). Nothing else ships at runtime.
- **Proven wire compatibility:** a golden interop test suite runs every
  session lifecycle (connect, approve, custom requests, update, kill) in both
  orientations against the original `@walletconnect/client@1.8.0` as an
  oracle, plus byte-level encryption/HMAC interop vectors.
- **7-day dependency cooldown:** `minimumReleaseAge` is enforced so freshly
  published (potentially compromised) dependency versions cannot enter the
  lockfile for a week.
- **Hardened CI:** all GitHub Actions are SHA-pinned; CodeQL static analysis,
  OpenSSF Scorecard, and SBOM generation run on every change.
- **Provenance-verified releases:** publishing uses npm OIDC trusted
  publishing with Sigstore provenance — no long-lived npm tokens exist. See
  [RELEASING.md](https://github.com/perawallet/pera-walletconnect-ts/blob/main/RELEASING.md).

## Development

```sh
pnpm install          # install (frozen lockfile in CI)
pnpm build            # build ESM + CJS + .d.ts into packages/walletconnect/dist
pnpm test             # run the full vitest suite (incl. interop tests)
pnpm test:coverage    # tests with coverage
pnpm lint             # oxlint
pnpm format           # oxfmt
pnpm typecheck        # tsc --noEmit
```

This is a pnpm workspace; the single published package lives at
[`packages/walletconnect`](https://github.com/perawallet/pera-walletconnect-ts/tree/main/packages/walletconnect).
Node.js ≥ 22 is required.

## License

[Apache-2.0](https://github.com/perawallet/pera-walletconnect-ts/blob/main/LICENSE).
This project is a fork and modernization of
[WalletConnect v1.x](https://github.com/WalletConnect/walletconnect-monorepo)
(Copyright 2020 WalletConnect) — see
[NOTICE](https://github.com/perawallet/pera-walletconnect-ts/blob/main/NOTICE)
for full attribution.
