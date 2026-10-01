import { type FormEvent } from "react";

interface CreateAccountFormProps {
  username: string;
  password: string;
  busy: boolean;
  onUsernameChange: (value: string) => void;
  onPasswordChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

export function CreateAccountForm({
  username,
  password,
  busy,
  onUsernameChange,
  onPasswordChange,
  onSubmit,
}: CreateAccountFormProps) {
  return (
    <details style={{ marginTop: "0.5rem" }}>
      <summary>新增主揪帳號</summary>
      <form onSubmit={onSubmit} style={{ marginTop: "0.75rem" }}>
        <label className="field">
          <span>新主揪帳號</span>
          <input
            autoComplete="off"
            value={username}
            onChange={(event) => onUsernameChange(event.target.value)}
            required
          />
        </label>
        <label className="field">
          <span>新主揪密碼</span>
          <input
            type="password"
            autoComplete="new-password"
            minLength={12}
            value={password}
            onChange={(event) => onPasswordChange(event.target.value)}
            required
          />
        </label>
        <p style={{ fontSize: "0.85rem", color: "var(--muted)", margin: "0.25rem 0 0.5rem" }}>
          密碼至少 12 個字元。
        </p>
        <button className="action-button" disabled={busy} type="submit">
          建立主揪
        </button>
      </form>
    </details>
  );
}
