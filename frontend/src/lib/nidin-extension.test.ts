import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  detectExtension,
  hasExtensionApi,
  NidinPairingManager,
  PROTOCOL_VERSION,
} from "./nidin-extension";

describe("Nidin Extension Frontend Client", () => {
  const fakeExtensionId = "fake-extension-id-12345";

  beforeEach(() => {
    vi.stubGlobal("window", {
      chrome: {
        runtime: {
          sendMessage: vi.fn(),
          connect: vi.fn(),
          lastError: null,
        },
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("detects extension presence via HELLO handshake", async () => {
    const sendMessageMock = vi.fn((extId, msg, cb) => {
      expect(extId).toBe(fakeExtensionId);
      expect(msg.type).toBe("HELLO");
      expect(msg.protocolVersion).toBe(PROTOCOL_VERSION);
      cb({ type: "HELLO_ACK", protocolVersion: 1, version: "0.1.0", status: "ok" });
    });

    window.chrome.runtime.sendMessage = sendMessageMock;

    const result = await detectExtension(fakeExtensionId);
    expect(result.installed).toBe(true);
    expect(result.version).toBe("0.1.0");
  });

  it("handles extension missing or lastError gracefully", async () => {
    window.chrome.runtime.sendMessage = vi.fn((extId, msg, cb) => {
      window.chrome.runtime.lastError = { message: "Could not establish connection." };
      cb(undefined);
    });

    const result = await detectExtension(fakeExtensionId);
    expect(result.installed).toBe(false);
    expect(result.error).toContain("Could not establish connection");
  });

  it("manages pairing flow lifecycle: init -> start -> candidate -> confirm -> finish", async () => {
    const postMessageMock = vi.fn();
    const disconnectMock = vi.fn();
    let messageListener: ((msg: any) => void) | null = null;

    const fakePort = {
      postMessage: postMessageMock,
      disconnect: disconnectMock,
      onMessage: {
        addListener: vi.fn((fn) => {
          messageListener = fn;
        }),
      },
      onDisconnect: {
        addListener: vi.fn(),
      },
    };

    window.chrome.runtime.connect = vi.fn().mockReturnValue(fakePort);

    // Mock API fetch
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === "/api/leader/auth/pairings") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            flow_id: "flow-abc-123",
            state: "state-secret-456",
            expires_at: Date.now() / 1000 + 300,
            user_summary: "admin",
            credential_revision: 0,
          }),
        });
      }
      if (url.includes("/lease")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: "pending",
            lease_expires_at: Date.now() / 1000 + 10,
          }),
        });
      }
      if (url.includes("/confirm")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            is_authenticated: true,
            user_id: "12345678",
            masked_token: "eyJh...load",
          }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    vi.stubGlobal("fetch", fetchMock);

    const manager = new NidinPairingManager(fakeExtensionId);
    expect(manager.status).toBe("ready");

    // 1. 啟動配對
    await manager.startPairing();
    expect(fetchMock).toHaveBeenCalledWith("/api/leader/auth/pairings", expect.anything());
    expect(window.chrome.runtime.connect).toHaveBeenCalledWith(fakeExtensionId);
    expect(postMessageMock).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "START_PAIRING",
        flowId: "flow-abc-123",
        state: "state-secret-456",
      })
    );
    expect(manager.status).toBe("waiting_login");

    // 2. 收到候選憑證
    expect(messageListener).toBeTruthy();
    messageListener!({
      type: "CREDENTIAL_CANDIDATE",
      flowId: "flow-abc-123",
      state: "state-secret-456",
      token: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload",
      userId: "12345678",
    });

    expect(manager.status).toBe("awaiting_confirmation");
    expect(manager.getCandidate()?.token).toBe("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload");

    // 3. 確認綁定
    const confirmResult = await manager.confirmBinding();
    expect(confirmResult.success).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/leader/auth/pairings/flow-abc-123/confirm",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          state: "state-secret-456",
          token: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload",
          user_id: "12345678",
        }),
      })
    );
    expect(postMessageMock).toHaveBeenCalledWith({
      type: "FINISH_PAIRING",
      flowId: "flow-abc-123",
      state: "state-secret-456",
    });
    expect(manager.status).toBe("saved");

    manager.destroy();
  });
});
