import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { speak, stopSpeaking, toSpeechText } from "./speech";

describe("toSpeechText", () => {
  it("用杯數與金額取代冗長的規格描述", () => {
    const reply = "已為您解析點單，請確認品項與規格：珍珠奶茶 2 杯 半糖少冰、四季春 1 杯 微糖微冰";
    expect(toSpeechText(reply, 2, 165)).toBe("幫你點了 2 筆，總共 165 元，確認一下好嗎？");
  });

  it("沒有解析結果時保留原句", () => {
    expect(toSpeechText("請問要幾分糖？")).toBe("請問要幾分糖？");
  });

  it("去掉 emoji，避免唸出奇怪的字", () => {
    expect(toSpeechText("⚠️ 找不到此品項。")).toBe("找不到此品項。");
  });

  it("過長的句子取第一句", () => {
    const long = `${"很長的說明".repeat(12)}。第二句不需要唸。`;
    const spoken = toSpeechText(long);
    expect(spoken).not.toContain("第二句");
  });

  it("空字串進、空字串出", () => {
    expect(toSpeechText("   ")).toBe("");
  });
});

describe("speak / stopSpeaking", () => {
  const originalFetch = globalThis.fetch;
  const originalAudio = globalThis.Audio;
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;

  let played: string[];

  beforeEach(() => {
    played = [];
    URL.createObjectURL = vi.fn(() => "blob:fake") as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
    class FakeAudio {
      src: string;
      private endedHandler: (() => void) | null = null;
      constructor(src: string) {
        this.src = src;
        played.push(src);
      }
      addEventListener(type: string, handler: () => void) {
        if (type === "ended") this.endedHandler = handler;
      }
      pause() {}
      play() {
        // 立刻播完，讓 speak() 的 promise 收尾
        queueMicrotask(() => this.endedHandler?.());
        return Promise.resolve();
      }
    }
    globalThis.Audio = FakeAudio as unknown as typeof Audio;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    globalThis.Audio = originalAudio;
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  });

  it("合成失敗時不播放，也不丟例外", async () => {
    globalThis.fetch = vi.fn(async () => new Response("", { status: 502 })) as typeof fetch;
    await expect(speak("測試")).resolves.toBeUndefined();
    expect(played).toHaveLength(0);
  });

  it("空字串不會打 API", async () => {
    const spy = vi.fn();
    globalThis.fetch = spy as unknown as typeof fetch;
    await speak("   ");
    expect(spy).not.toHaveBeenCalled();
  });

  it("等 fetch 期間被中斷就不出聲", async () => {
    let release: (value: Response) => void = () => {};
    globalThis.fetch = vi.fn(
      () => new Promise<Response>((resolve) => { release = resolve; }),
    ) as unknown as typeof fetch;

    const pending = speak("會被打斷的一句");
    stopSpeaking();                                   // 音檔還沒回來就喊停
    release(new Response(new Blob(["audio"])));
    await pending;

    expect(played).toHaveLength(0);
  });

  it("沒被打斷就正常播放", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(new Blob(["audio"])),
    ) as unknown as typeof fetch;

    await speak("正常播放");
    expect(played).toEqual(["blob:fake"]);
  });
});
