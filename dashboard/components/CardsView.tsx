"use client";

import { useCallback, useRef, useState } from "react";
import styles from "./CardsView.module.css";

export type CardItem = {
  name: string;
  date: string;
  plate: string;
  url: string;
  bytes: number;
};

/** "2026-08-12" -> "Wed 12 August". Fixed to UTC so the label cannot shift
 *  by a day depending on where the phone is. */
function longDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  return d.toLocaleDateString("en-GB", {
    weekday: "short", day: "numeric", month: "long", timeZone: "UTC",
  });
}

type SaveState = "idle" | "working" | "done" | "failed";

/**
 * SAVING ON A PHONE IS THE WHOLE POINT OF THIS PAGE, and a plain
 * `<a download>` does not do it: the download attribute is ignored for
 * cross-origin URLs, so on iOS it navigates to the PNG instead of saving it.
 *
 * Fetching the bytes ourselves and handing a File to the Web Share API opens
 * the native sheet — Save Image, or straight into the X app — which is the
 * flow an iPhone user already knows. Desktop has no share sheet, so there it
 * falls back to an object URL, which IS same-origin and so does download.
 *
 * The bucket sends Access-Control-Allow-Origin: *, which is what makes the
 * fetch legal.
 */
async function saveOrShare(url: string, filename: string): Promise<void> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`);
  const blob = await res.blob();

  const file = new File([blob], filename, { type: blob.type || "image/png" });
  const nav = navigator as Navigator & {
    canShare?: (d: ShareData) => boolean;
    share?: (d: ShareData) => Promise<void>;
  };
  if (nav.canShare?.({ files: [file] }) && nav.share) {
    try {
      await nav.share({ files: [file] });
      return;
    } catch (err) {
      // A user dismissing the sheet throws AbortError. That is not a failure
      // and must not fall through to a surprise download.
      if ((err as Error)?.name === "AbortError") return;
    }
  }

  const obj = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = obj;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(obj);
}

function Card({ item, eager }: { item: CardItem; eager: boolean }) {
  const [state, setState] = useState<SaveState>("idle");

  const onSave = useCallback(async () => {
    setState("working");
    try {
      await saveOrShare(item.url, item.name);
      setState("done");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2600);
  }, [item]);

  return (
    <figure className={styles.card}>
      <a href={item.url} target="_blank" rel="noreferrer" className={styles.shot}>
        {/* Not next/image: these are 1080x1080 PNGs on a public bucket and the
            optimizer would re-encode a file whose whole job is to be posted
            byte-for-byte. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={item.url} alt={`Backfist Bets card, ${item.plate}, ${item.date}`}
             width={1080} height={1080} loading={eager ? "eager" : "lazy"} />
      </a>
      <figcaption className={styles.foot}>
        <span className={styles.plate}>{item.plate}</span>
        <button type="button" onClick={onSave} className={styles.save}
                disabled={state === "working"}>
          {state === "working" ? "Saving…"
            : state === "done" ? "Saved"
            : state === "failed" ? "Try again"
            : "Save / Share"}
        </button>
      </figcaption>
    </figure>
  );
}

/**
 * The night's X post, ready to paste.
 *
 * The whole point is one tap, so the button copies and says so.
 *
 * IT ALSO HAS TO SURVIVE THE CLIPBOARD BEING REFUSED. `writeText` can throw
 * NotAllowedError even in a focused, secure context that exposes the API —
 * browser policy, a locked-down profile, an http:// host. Measured, not
 * assumed: it threw exactly that on a prod build over localhost. The first
 * version caught it and set state back to idle, so the button did nothing
 * at all when pressed, which is the worst outcome available. Now the fallback
 * SELECTS the post so the keyboard shortcut works, and the label says so.
 */
function PostBlock({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "manual">("idle");
  const bodyRef = useRef<HTMLParagraphElement>(null);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
      setTimeout(() => setState("idle"), 2400);
      return;
    } catch {
      // fall through to selecting it by hand
    }
    const el = bodyRef.current;
    if (el) {
      const range = document.createRange();
      range.selectNodeContents(el);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
    }
    setState("manual");
  }, [text]);

  return (
    <div className={styles.post}>
      <div className={styles.postHead}>
        <span className={styles.postLabel}>Post for X</span>
        <button type="button" onClick={onCopy} className={styles.copy}>
          {state === "copied" ? "Copied"
            : state === "manual" ? "Selected — press Ctrl/⌘+C"
            : "Copy"}
        </button>
      </div>
      <p className={styles.postBody} ref={bodyRef}>{text}</p>
    </div>
  );
}

export function CardsView({ items, posts, configured }:
  { items: CardItem[]; posts: Record<string, string>; configured: boolean }) {
  const nights = Array.from(new Set(items.map((i) => i.date)));

  return (
    <main className={styles.page}>
      <header className={styles.head}>
        <a href="/" className={styles.back}>← Board</a>
        <h1 className={styles.title}>Cards</h1>
        <p className={styles.blurb}>
          Square graphics for X, built from the ledger. Tap a card to open it
          full size, or <strong>Save / Share</strong> to put it in your camera
          roll. Each night’s <strong>Post for X</strong> is written to go with
          them — hit <strong>Copy</strong> and paste it straight in.
        </p>
      </header>

      {!configured && (
        <p className={styles.empty}>
          Supabase isn’t configured in this environment, so there’s nothing to
          list. The cards are still on the machine that made them.
        </p>
      )}

      {configured && items.length === 0 && (
        <p className={styles.empty}>
          No cards published yet. Run{" "}
          <code>python tools/cards/make_card.py --date {new Date()
            .toISOString().slice(0, 10)} --plate all --publish</code>{" "}
          and they’ll appear here.
        </p>
      )}

      {nights.map((date) => (
        <section key={date} className={styles.night}>
          <h2 className={styles.nightTitle}>{longDate(date)}</h2>
          {posts[date] && <PostBlock text={posts[date]} />}
          <div className={styles.grid}>
            {/* The newest night is the reason the operator opened the page —
                load it eagerly. Lazy-loading is for the archive below it. */}
            {items.filter((i) => i.date === date)
                  .map((i) => <Card key={i.name} item={i}
                                    eager={date === nights[0]} />)}
          </div>
        </section>
      ))}
    </main>
  );
}
