/**
 * 比比拉布的語音輸出。
 *
 * 實測 EdgeTTS 唸完一整句帶規格的回覆要 10 秒，使用者早就看完預覽卡了，
 * 所以這裡只唸摘要：明細留給眼睛看卡片，耳朵只聽結論。
 */

const MAX_SPEECH_LENGTH = 60;

let currentAudio: HTMLAudioElement | null = null;
// 每次 stopSpeaking 就前進一代，讓還在等 fetch 的舊 speak() 知道自己已被取消
let generation = 0;

/** 把給眼睛看的回覆濃縮成給耳朵聽的一句話。 */
export function toSpeechText(text: string, itemCount = 0, totalPrice = 0): string {
  const cleaned = text
    .replace(/[⚠️🎉📋🥤]/gu, "")
    .replace(/\s+/g, " ")
    .trim();

  // 有解析結果時，用杯數與金額取代冗長的規格描述
  if (itemCount > 0) {
    return `幫你點了 ${itemCount} 筆，總共 ${totalPrice} 元，確認一下好嗎？`;
  }

  if (cleaned.length <= MAX_SPEECH_LENGTH) return cleaned;

  // 過長就取第一個句子，避免唸到一半被打斷更尷尬
  const firstSentence = cleaned.split(/[。！？\n]/)[0];
  if (firstSentence && firstSentence.length <= MAX_SPEECH_LENGTH) return firstSentence;

  return cleaned.slice(0, MAX_SPEECH_LENGTH);
}

/** 停掉正在播的語音。使用者一動作就該閉嘴，不要自言自語。 */
export function stopSpeaking(): void {
  // 就算目前沒在播，也要讓等待中的 speak() 作廢
  generation += 1;
  if (!currentAudio) return;
  try {
    currentAudio.pause();
    const url = currentAudio.src;
    if (url.startsWith("blob:")) URL.revokeObjectURL(url);
  } catch {
    // 忽略 pause 或 revokeObjectURL 失敗
  } finally {
    currentAudio = null;
  }
}

/**
 * 唸一句話。語音是附加品，失敗一律安靜吞掉——文字已經顯示在畫面上了。
 */
export async function speak(text: string, signal?: AbortSignal): Promise<void> {
  const trimmed = text.trim();
  if (!trimmed) return;

  stopSpeaking();
  const mine = generation;

  let objectUrl = "";
  try {
    const response = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: trimmed }),
      signal,
    });
    if (!response.ok) return;

    const blob = await response.blob();
    // 等 fetch 的期間被中斷就不要再出聲
    if (signal?.aborted || mine !== generation) return;

    objectUrl = URL.createObjectURL(blob);
    const audio = new Audio(objectUrl);
    currentAudio = audio;

    await new Promise<void>((resolve) => {
      audio.addEventListener("ended", () => resolve(), { once: true });
      audio.addEventListener("error", () => resolve(), { once: true });
      // 播放被瀏覽器自動播放政策擋下時也要收尾
      audio.play().catch(() => resolve());
    });
  } catch {
    // 網路錯誤、AbortError：靜默略過
  } finally {
    if (currentAudio?.src === objectUrl) currentAudio = null;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  }
}
