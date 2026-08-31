// x402 payment client for VailAudit — Algorand Testnet via the GoPlausible
// facilitator. Bundle this with `npm run build` (esbuild) into
// dist/x402-client.bundle.js and load that <script> from VailAudit.html
// instead of the esm.sh runtime-import version.
import { x402Client } from "@x402-avm/core/client";
import { registerExactAvmScheme } from "@x402-avm/avm/exact/client";
import { PeraWalletConnect } from "@perawallet/connect";

const peraWallet = new PeraWalletConnect({ chainId: 416002 }); // Algorand Testnet
let activeAccount = null;

function short(addr) {
  return addr ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : "";
}

function updateWalletUI() {
  const walletBtn = document.getElementById("walletBtn");
  const walletStatus = document.getElementById("walletStatus");
  if (!walletBtn || !walletStatus) return;

  if (activeAccount) {
    walletBtn.textContent = short(activeAccount);
    walletStatus.textContent = `Connected: ${short(activeAccount)} (Testnet)`;
  } else {
    walletBtn.textContent = "Connect Wallet";
    walletStatus.textContent = "Not connected — required to pay for an audit";
  }
}

async function connectWallet() {
  try {
    const accounts = await peraWallet.connect();
    peraWallet.connector?.on("disconnect", () => {
      activeAccount = null;
      updateWalletUI();
    });
    activeAccount = accounts[0];
  } catch (err) {
    if (err?.data?.type !== "CONNECT_MODAL_CLOSED") {
      console.error("Wallet connect failed:", err);
    }
  }
  updateWalletUI();
}

function initWalletButton() {
  const walletBtn = document.getElementById("walletBtn");
  if (!walletBtn) return;

  walletBtn.addEventListener("click", () => {
    activeAccount ? peraWallet.disconnect() : connectWallet();
  });

  peraWallet.reconnectSession().then((accounts) => {
    if (accounts.length) {
      activeAccount = accounts[0];
      peraWallet.connector?.on("disconnect", () => {
        activeAccount = null;
        updateWalletUI();
      });
    }
    updateWalletUI();
  });
}

function buildX402Client() {
  if (!activeAccount) {
    throw new Error("Connect an Algorand testnet wallet first.");
  }
  const signer = {
    address: activeAccount,
    signTransactions: (txns, indexesToSign) =>
      peraWallet.signTransaction([txns], indexesToSign),
  };
  const client = new x402Client();
  registerExactAvmScheme(client, { signer });
  return client;
}

// Payment-gated replacement for a plain fetch() to /api/audit.
// onStatus(msg) is an optional callback used to narrate progress in the UI.
async function x402CallBackendAudit(apiBaseUrl, form, onStatus) {
  const client = buildX402Client();
  onStatus?.("[Payment] Wallet connected — sending request…");

  // x402Client.fetch() transparently handles the 402 → sign → retry loop
  // and returns the final settled response.
  const resp = await client.fetch(`${apiBaseUrl}/api/audit`, {
    method: "POST",
    body: form,
  });

  if (!resp.ok) throw new Error(`Backend responded ${resp.status}`);

  const settlement = resp.headers.get("X-PAYMENT-RESPONSE");
  if (settlement) {
    onStatus?.(
      "[Payment] Settled on Algorand Testnet — view on Lora: https://lora.algokit.io/testnet"
    );
  }
  return resp.json();
}

// Expose on window so the page's non-module app script can call these.
window.x402CallBackendAudit = x402CallBackendAudit;
initWalletButton();
