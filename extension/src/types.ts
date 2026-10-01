export const PROTOCOL_VERSION = 1;
export const EXTENSION_VERSION = "0.1.0";

export interface HelloMessage {
  type: "HELLO";
  protocolVersion: number;
}

export interface HelloAckMessage {
  type: "HELLO_ACK";
  protocolVersion: number;
  version: string;
  status: "ok";
}

export interface StartPairingMessage {
  type: "START_PAIRING";
  protocolVersion: number;
  flowId: string;
  state: string;
  expiresAt: number;
}

export interface PairingLeaseMessage {
  type: "PAIRING_LEASE";
  protocolVersion: number;
  flowId: string;
  state: string;
  leaseExpiresAt: number;
}

export interface PairingStatusMessage {
  type: "PAIRING_STATUS";
  flowId: string;
  status: "opening" | "waiting_login" | "awaiting_confirmation" | "tab_closed" | "error";
  nidinTabId?: number;
  error?: string;
}

export interface CredentialCandidateMessage {
  type: "CREDENTIAL_CANDIDATE";
  flowId: string;
  state: string;
  token: string;
  userId: string;
}

export interface FinishPairingMessage {
  type: "FINISH_PAIRING";
  flowId: string;
  state: string;
}

export interface CancelPairingMessage {
  type: "CANCEL_PAIRING";
  flowId: string;
  state: string;
}

export interface CloseNidinTabMessage {
  type: "CLOSE_NIDIN_TAB";
  flowId: string;
  state: string;
}

export type WebToExtensionMessage =
  | HelloMessage
  | StartPairingMessage
  | PairingLeaseMessage
  | FinishPairingMessage
  | CancelPairingMessage
  | CloseNidinTabMessage;

export type ExtensionToWebMessage =
  | HelloAckMessage
  | PairingStatusMessage
  | CredentialCandidateMessage;

export interface ContentToCheckMessage {
  type: "CHECK_PAIRING";
}

export interface WorkerCheckResponse {
  shouldPoll: boolean;
  flowId?: string;
  waitingLease?: boolean;
}

export interface ContentCandidatePayload {
  type: "CONTENT_CANDIDATE";
  token: string;
  userId: string;
}

export interface PairingSessionData {
  flowId: string;
  state: string;
  expiresAt: number;
  webOrigin: string;
  webTabId?: number;
  nidinTabId?: number;
  status: "opening" | "waiting_login" | "awaiting_confirmation" | "finished" | "cancelled";
  leaseExpiresAt: number;
  candidateReceived: boolean;
}
