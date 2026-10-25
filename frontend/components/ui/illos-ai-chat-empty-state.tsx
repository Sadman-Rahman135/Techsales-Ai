"use client";

// Empty state for an AI chat or assistant.
// The illustration is Streaming tokens (Shader) from Illos (https://illos.dev).
// It loops while it is on screen, pauses off screen, and stays still for prefers-reduced-motion.
//
// Adapted for KnowledgeAI: the project's semantic tokens instead of shadcn's, a lucide send icon, an optional
// composer (the chat page has its own), suggestions that can ask at once, and a configurable heading level. The
// illustration's colours come from the --illos-* tokens in app/globals.css (both themes).
//
// License
// The component code in this file (layout, props, play logic) is free to use, change and share under the MIT license.
// The illustration inside it (the SVG markup and its animation) is not covered by MIT. It belongs to Illos and is
// licensed under the Illos free license, https://illos.dev/license: you can use it in personal and commercial work,
// including client projects, with no credit needed, and you may modify it. Not allowed: redistributing or selling it
// as an asset (packs, illustration or icon sites, or other libraries), or using it to build a competing illustration
// library.
import { ArrowUp } from "lucide-react";
import { useEffect, useId, useRef, type CSSProperties, type ReactNode, useState } from "react";

const cx = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(" ");

type ShaderIllustration = "streaming-tokens";

export type IllosAiChatEmptyStateProps = {
  title?: string;
  description?: ReactNode;
  suggestions?: string[];
  /** Read by screen readers before the suggestion chips. */
  suggestionsLabel?: string;
  /** A chip was chosen. Without it, the chip fills the built-in composer instead. */
  onSuggestion?: (suggestion: string) => void;
  /** The built-in message box; off when the page has its own composer. */
  showComposer?: boolean;
  placeholder?: string;
  onSubmit?: (message: string) => void;
  titleAs?: "h1" | "h2" | "h3";
  className?: string;
};

export function IllosAiChatEmptyState({
  title = "Ask anything about your data",
  description = "Answers stream in as they are written, with the sources they came from.",
  suggestions = ["Which customers grew fastest last month?", "Summarise yesterday's error logs", "Draft a reply to the newest ticket"],
  suggestionsLabel = "Try asking",
  onSuggestion,
  showComposer = true,
  placeholder = "Ask a question",
  onSubmit,
  titleAs: Title = "h3",
  className,
}: IllosAiChatEmptyStateProps) {
  const [value, setValue] = useState("");
  const id = useId();
  return (
    <div className={cx("flex w-full flex-col overflow-hidden rounded-card border border-border bg-surface", className)}>
      <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6 py-10 text-center">
        <ShaderArt
          name="streaming-tokens"
          className="w-full max-w-[420px] [--illos-ground:var(--color-surface)] [--illos-ink:var(--color-fg)]"
        />
        <div className="flex max-w-md flex-col gap-1.5">
          <Title className="font-heading text-2xl font-semibold tracking-tight text-fg">{title}</Title>
          <p className="text-base leading-relaxed text-fg-muted">{description}</p>
        </div>
        {suggestions.length ? (
          <div className="flex max-w-xl flex-col items-center gap-2">
            <p id={`${id}-suggestions`} className="text-sm font-semibold text-fg-muted">
              {suggestionsLabel}
            </p>
            <ul aria-labelledby={`${id}-suggestions`} className="flex flex-wrap justify-center gap-2">
              {suggestions.map((s) => (
                <li key={s}>
                  <button
                    type="button"
                    onClick={() => (onSuggestion ? onSuggestion(s) : setValue(s))}
                    className="min-h-11 rounded-full border border-border-strong bg-surface px-4 text-sm text-fg transition-colors duration-150 hover:border-accent hover:text-accent"
                  >
                    {s}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
      {showComposer ? (
        <form
          className="flex items-center gap-2 border-t border-border p-3"
          onSubmit={(e) => {
            e.preventDefault();
            const text = value.trim();
            if (!text) return;
            onSubmit?.(text);
            setValue("");
          }}
        >
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={placeholder}
            aria-label="Message"
            className="min-h-11 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg placeholder:text-fg-muted"
          />
          <button
            type="submit"
            disabled={!value.trim()}
            aria-label="Send"
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-control bg-accent text-on-accent transition-colors hover:bg-accent/90 disabled:pointer-events-none disabled:opacity-50"
          >
            <ArrowUp className="size-4" aria-hidden="true" />
          </button>
        </form>
      ) : null}
    </div>
  );
}

export default IllosAiChatEmptyState;

const SHADER_SVGS: Record<ShaderIllustration, string> = {
  "streaming-tokens": `<svg class="sh-streaming-tokens" xmlns="http://www.w3.org/2000/svg" width="720" height="460" viewBox="0 0 720 460" fill="none" role="img" aria-labelledby="sh-streaming-tokens-t">
<title id="sh-streaming-tokens-t">Streaming tokens</title>
<style>@scope (svg.sh-streaming-tokens) {
:scope{--_ground:#ffffff;--_ink:#2a2a2a;--_ramp-1:#16645a;--_ramp-2:#1daf9c;--_ramp-3:#3fdec9;--_ramp-4:#c2f5ee;--_mark:#178c7d;--_spark:#0b463e}
:scope:is(.dark *){--_ground:#101112;--_ink:#dadadc;--_ramp-1:#84ebdd;--_ramp-2:#3fdec9;--_ramp-3:#1daf9c;--_ramp-4:#1a7569;--_mark:#3fdec9;--_spark:#ffffff}
:scope{--g:var(--illos-ground,var(--_ground));--k:var(--illos-ink,var(--_ink))}
.b0,.b1,.b2,.b3{fill:none;stroke-dasharray:7 2.4}
.b0{stroke:var(--illos-ramp-1,var(--_ramp-1))}.b1{stroke:var(--illos-ramp-2,var(--_ramp-2))}.b2{stroke:var(--illos-ramp-3,var(--_ramp-3))}.b3{stroke:var(--illos-ramp-4,var(--_ramp-4));opacity:.55}
.sc{fill:none;stroke:color-mix(in srgb,var(--illos-ramp-4,var(--_ramp-4)) 18%,var(--g));stroke-width:.8}
.bm{fill:none;stroke:var(--illos-ramp-2,var(--_ramp-2));stroke-width:2.4;stroke-dasharray:5 2.4}
.sp{fill:none;stroke:var(--illos-spark,var(--_spark));stroke-width:1.2;stroke-linecap:round;opacity:.85}
.gr{stroke:color-mix(in srgb,var(--k) 9%,var(--g));stroke-width:1}
.bx,.ln,.lv{fill:none;stroke:var(--illos-mark,var(--_mark));stroke-width:1.2}
.dot{fill:var(--illos-mark,var(--_mark))}.cf{fill:var(--illos-mark,var(--_mark))}
.ct{fill:none;stroke:var(--g);stroke-width:1.25;stroke-linecap:round;stroke-linejoin:round}

@keyframes sh-streaming-tokens_clock{0%{opacity:0}100%{opacity:0}}
.sh-clock{animation:sh-streaming-tokens_clock 4.2s linear both}
*{animation-iteration-count:1!important;animation-fill-mode:both!important;animation-play-state:var(--illos-play,running)!important}
@media (prefers-reduced-motion: reduce){*{animation:none!important}}
}
</style>
<g class="sh-still"><path class="sc" d="M4 0V460M11 0V460M18 0V460M25 0V460M32 0V460M39 0V460M46 0V460M53 0V460M60 0V460M67 0V460M74 0V460M81 0V460M88 0V460M95 0V460M102 0V460M109 0V460M116 0V460M123 0V460M130 0V460M137 0V460M144 0V460M151 0V460M158 0V460M165 0V460M172 0V460M179 0V460M186 0V460M193 0V460M200 0V460M207 0V460M214 0V460M221 0V460M228 0V460M235 0V460M242 0V460M249 0V460M256 0V460M263 0V460M270 0V460M277 0V460M284 0V460M291 0V460M298 0V460M305 0V460M312 0V460M319 0V460M326 0V460M333 0V460M340 0V460M347 0V460M354 0V460M361 0V460M368 0V460M375 0V460M382 0V460M389 0V460M396 0V460M403 0V460M410 0V460M417 0V460M424 0V460M431 0V460M438 0V460M445 0V460M452 0V460M459 0V460M466 0V460M473 0V460M480 0V460M487 0V460M494 0V460M501 0V460M508 0V460M515 0V460M522 0V460M529 0V460M536 0V460M543 0V460M550 0V460M557 0V460M564 0V460M571 0V460M578 0V460M585 0V460M592 0V460M599 0V460M606 0V460M613 0V460M620 0V460M627 0V460M634 0V460M641 0V460M648 0V460M655 0V460M662 0V460M669 0V460M676 0V460M683 0V460M690 0V460M697 0V460M704 0V460M711 0V460M718 0V460"/><path class="gr" d="M0 0V460M96 0V460M192 0V460M288 0V460M384 0V460M480 0V460M576 0V460M672 0V460M0 0H720M0 96H720M0 192H720M0 288H720M0 384H720"/><path class="b0" d="M81 216.2V232.6M88 216.2V232.6M95 216.2V242M102 235V242M179 225.6V232.6M256 235V242M326 216.2V223.2M333 216.2V223.2" stroke-width="3.6"/><path class="b1" d="M67 216.2V242M74 206.8V251.4M81 206.8V213.8M81 235V251.4M88 206.8V213.8M88 235V251.4M95 206.8V213.8M95 244.4V251.4M102 216.2V232.6M102 244.4V251.4M109 216.2V232.6M172 225.6V232.6M186 235V242M249 244.4V251.4M256 244.4V251.4M263 235V242M340 225.6V232.6M382 291.4V307.8M389 291.4V298.4M417 159.8V166.8M417 206.8V213.8M417 244.4V251.4M424 159.8V166.8M438 159.8V166.8M438 197.4V213.8M438 244.4V251.4M452 159.8V166.8M452 206.8V213.8M459 206.8V213.8M459 244.4V251.4M466 159.8V166.8M529 159.8V166.8M529 244.4V251.4M543 159.8V166.8M557 159.8V166.8M557 244.4V251.4M564 159.8V166.8M564 244.4V251.4M571 159.8V166.8M592 159.8V166.8M599 159.8V166.8M599 244.4V251.4M634 159.8V166.8M634 206.8V213.8M641 159.8V166.8M641 206.8V213.8" stroke-width="2.7"/><path class="b2" d="M60 216.2V251.4M67 206.8V213.8M67 244.4V260.8M74 253.8V260.8M81 197.4V204.4M81 253.8V260.8M88 197.4V204.4M88 253.8V260.8M95 197.4V204.4M95 253.8V260.8M102 206.8V213.8M102 253.8V260.8M109 206.8V213.8M109 235V251.4M116 206.8V242M123 216.2V242M130 225.6V232.6M137 216.2V223.2M144 216.2V223.2M158 216.2V223.2M165 216.2V223.2M172 216.2V223.2M186 225.6V232.6M200 244.4V251.4M235 253.8V260.8M242 253.8V260.8M249 253.8V260.8M270 225.6V232.6M319 216.2V223.2M333 225.6V232.6M340 216.2V223.2M347 225.6V232.6M354 225.6V232.6M389 150.4V166.8M389 197.4V213.8M389 244.4V260.8M389 300.8V307.8M410 159.8V166.8M410 197.4V213.8M410 244.4V260.8M417 150.4V157.4M417 197.4V204.4M417 253.8V260.8M424 197.4V213.8M424 244.4V260.8M431 150.4V166.8M431 197.4V213.8M431 244.4V260.8M438 150.4V157.4M438 253.8V260.8M445 150.4V166.8M445 197.4V213.8M445 244.4V260.8M452 150.4V157.4M452 197.4V204.4M452 244.4V260.8M459 150.4V166.8M459 197.4V204.4M459 253.8V260.8M466 150.4V157.4M466 197.4V213.8M466 244.4V260.8M480 159.8V166.8M480 197.4V213.8M480 244.4V251.4M487 159.8V166.8M487 206.8V213.8M487 244.4V260.8M501 159.8V166.8M501 197.4V213.8M501 244.4V251.4M508 159.8V166.8M508 206.8V213.8M508 244.4V251.4M522 159.8V166.8M522 197.4V213.8M522 244.4V260.8M529 150.4V157.4M529 197.4V213.8M529 253.8V260.8M543 150.4V157.4M543 197.4V213.8M543 244.4V260.8M557 197.4V213.8M557 253.8V260.8M564 150.4V157.4M564 197.4V213.8M564 253.8V260.8M571 150.4V157.4M571 197.4V213.8M571 244.4V260.8M578 159.8V166.8M578 197.4V213.8M578 244.4V260.8M585 159.8V166.8M585 197.4V213.8M585 244.4V260.8M592 197.4V213.8M592 244.4V260.8M599 150.4V157.4M599 197.4V213.8M599 253.8V260.8M627 150.4V166.8M627 197.4V213.8M627 244.4V260.8M634 150.4V157.4M634 197.4V204.4M634 244.4V260.8M641 150.4V157.4M641 197.4V204.4M641 244.4V260.8" stroke-width="1.8"/><path class="b3" d="M46 225.6V242M53 206.8V251.4M60 197.4V213.8M60 253.8V260.8M67 197.4V204.4M67 263.2V270.2M74 188V204.4M74 263.2V270.2M81 188V195M81 263.2V270.2M88 188V195M88 263.2V270.2M95 188V195M95 263.2V270.2M102 188V204.4M102 263.2V270.2M109 197.4V204.4M109 253.8V270.2M116 197.4V204.4M116 244.4V260.8M123 206.8V213.8M123 244.4V260.8M130 206.8V223.2M130 235V242M137 225.6V232.6M144 225.6V232.6M151 216.2V223.2M165 225.6V232.6M179 216.2V223.2M179 235V242M193 235V251.4M207 244.4V260.8M214 253.8V260.8M221 253.8V270.2M228 253.8V270.2M242 244.4V251.4M249 235V242M263 225.6V232.6M270 235V242M277 216.2V232.6M284 206.8V223.2M291 206.8V223.2M298 206.8V223.2M305 206.8V223.2M312 206.8V223.2M319 206.8V213.8M326 206.8V213.8M326 225.6V232.6M347 216.2V223.2M410 150.4V157.4M417 291.4V298.4M424 150.4V157.4M445 291.4V298.4M480 150.4V157.4M480 253.8V260.8M487 150.4V157.4M487 197.4V204.4M501 150.4V157.4M501 253.8V260.8M508 150.4V157.4M508 197.4V204.4M508 253.8V260.8M522 150.4V157.4M529 291.4V298.4M557 150.4V157.4M571 291.4V298.4M578 150.4V157.4M578 291.4V298.4M585 150.4V157.4M585 291.4V298.4M592 150.4V157.4M627 291.4V298.4" stroke-width="1.1"/><path class="sp" d="M81 218.6v1.2M95 218.6v1.2M95 237.4v1.2M102 237.4v1.2M179 228v1.2"/></g>
<g class="box"><path class="bx" pathLength="1" d="M44 186H132V274H44Z"/><circle class="dot" cx="44" cy="186" r="2.2"/><circle class="dot" cx="132" cy="186" r="2.2"/><circle class="dot" cx="44" cy="274" r="2.2"/><circle class="dot" cx="132" cy="274" r="2.2"/><g class="chip"><rect class="cf" x="44" y="186" width="46.8" height="14.9"/><path class="ct" d="M49 196.9L49 190L51.3 193.45L53.6 190L53.6 196.9M57.51 190L59.81 190L60.96 191.15L60.96 195.75L59.81 196.9L57.51 196.9L56.36 195.75L56.36 191.15L57.51 190M63.72 190L63.72 196.9L67.17 196.9L68.32 195.75L68.32 191.15L67.17 190L63.72 190M75.68 190L71.08 190L71.08 196.9L75.68 196.9M71.08 193.45L74.53 193.45M78.44 190L78.44 196.9L83.04 196.9"/></g></g><g class="box"><path class="bx" pathLength="1" d="M360 112H670V332H360Z"/><circle class="dot" cx="360" cy="112" r="2.2"/><circle class="dot" cx="670" cy="112" r="2.2"/><circle class="dot" cx="360" cy="332" r="2.2"/><circle class="dot" cx="670" cy="332" r="2.2"/><g class="chip"><rect class="cf" x="360" y="112" width="68.88" height="14.9"/><path class="ct" d="M365 122.9L365 116L368.45 116L369.6 117.15L369.6 118.3L368.45 119.45L365 119.45M367.3 119.45L369.6 122.9M376.96 116L372.36 116L372.36 122.9L376.96 122.9M372.36 119.45L375.81 119.45M384.32 117.15L383.17 116L380.87 116L379.72 117.15L379.72 118.3L380.87 119.45L383.17 119.45L384.32 120.6L384.32 121.75L383.17 122.9L380.87 122.9L379.72 121.75M387.08 122.9L387.08 116L390.53 116L391.68 117.15L391.68 118.3L390.53 119.45L387.08 119.45M395.59 116L397.89 116L399.04 117.15L399.04 121.75L397.89 122.9L395.59 122.9L394.44 121.75L394.44 117.15L395.59 116M401.8 122.9L401.8 116L406.4 122.9L406.4 116M413.76 117.15L412.61 116L410.31 116L409.16 117.15L409.16 118.3L410.31 119.45L412.61 119.45L413.76 120.6L413.76 121.75L412.61 122.9L410.31 122.9L409.16 121.75M421.12 116L416.52 116L416.52 122.9L421.12 122.9M416.52 119.45L419.97 119.45"/></g></g>
<rect class="sh-clock" width="0" height="0"/>
<metadata class="illos-field" data-cycle="4.2"><![CDATA[float field(vec2 p, float t) {
  float ph = TAU * t / T;
  float v = blob(p, vec2(88.0, 230.0), 30.0, 1.05);
  // the ribbon: a travelling wave from the model to the response, pulses running along it (six a cycle)
  if (p.x > 116.0 && p.x < 356.0) {
    float env = sin(3.14159 * (p.x - 116.0) / 240.0);
    float y = 230.0 + 30.0 * env * sin(p.x * 0.035 - ph * 2.0);
    float pulse = pow(0.5 + 0.5 * sin(p.x * 0.08 - ph * 6.0), 4.0);
    v += exp(-pow(p.y - y, 2.0) / 64.0) * (0.3 + 0.75 * pulse);
  }
  // the response: four lines; the last is written by the cursor, then it all scrolls up a line
  if (p.x > 376.0 && p.x < 654.0 && p.y > 130.0 && p.y < 322.0) {
    float yy = p.y + 46.0 * sm(3.2, 3.8, t);
    float idx = floor((yy - 160.0 + 23.0) / 46.0);
    float ly = 160.0 + 46.0 * idx;
    float on = step(0.32, hash(vec2(floor(p.x / 7.0), 1.0)));
    float front = t < 0.3 ? 380.0 : t < 3.0 ? mix(380.0, 650.0, (t - 0.3) / 2.7) : 650.0;
    float filled = idx < 3.0 ? 1.0 : idx < 4.0 ? (t > 3.0 ? 1.0 : 1.0 - sm(front - 6.0, front + 4.0, p.x)) : 0.0;
    float band = exp(-pow(yy - ly, 2.0) / 49.0);
    v += band * (on * (0.55 + 0.35 * hash(vec2(floor(p.x / 7.0), 2.0))) * filled + 0.13 * (1.0 - filled) * on);
    float cx = t < 3.0 ? front : 380.0;
    float cyy = t < 3.0 ? 298.0 : 298.0 - 46.0 * sm(3.2, 3.8, t) + 46.0 * step(3.8, t);
    v += blob(p, vec2(cx + 3.0, cyy), 8.0, 1.2 * (t < 3.0 || t > 3.8 ? 1.0 : 0.0));
  }
  return v;
}]]></metadata>
</svg>`,
};
const LIVE = "<style>.illos-live{position:relative}.illos-live>svg{position:relative}.illos-live .sh-still{visibility:hidden}.illos-field-canvas{position:absolute;inset:0;width:100%;height:100%;display:block}</style>";

/** One Shader illustration, inline, with its live field drawn beneath the SVG. Decorative here. */
function ShaderArt({ name, className, style }: { name: ShaderIllustration; className?: string; style?: CSSProperties }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return undefined;
    const stopPlay = loopInView(ref.current);
    const stopField = mountShader(ref.current);
    return () => {
      stopField();
      stopPlay();
    };
  }, [name]);
  return (
    <div
      ref={ref}
      aria-hidden="true"
      className={cx("[&>svg]:h-auto [&>svg]:w-full", className)}
      style={{ "--illos-play": "paused", ...style } as CSSProperties}
      // A fixed string from this file, never user input.
      dangerouslySetInnerHTML={{ __html: LIVE + SHADER_SVGS[name] }}
    />
  );
}

/** Loops while a quarter of it is in view, restarting each cycle on the next frame, and pauses off screen. Still for prefers-reduced-motion. */
function loopInView(host: HTMLElement): () => void {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return () => {};
  const animations = () => host.getAnimations({ subtree: true });
  let visible = false;
  let stopped = false;
  let waiting = false;
  const loop = () => {
    if (waiting || stopped || !visible) return;
    const list = animations();
    if (!list.length) return;
    waiting = true;
    Promise.all(list.map((a) => a.finished)).then(
      () =>
        requestAnimationFrame(() => {
          waiting = false;
          if (stopped || !visible) return;
          for (const a of animations()) a.currentTime = 0; // seek only: play() takes a CSS animation out of CSS control
          loop();
        }),
      () =>
        requestAnimationFrame(() => {
          waiting = false; // an animation was cancelled: pick the loop back up
          loop();
        }),
    );
  };
  const io = new IntersectionObserver(
    (entries) => {
      visible = entries.some((e) => e.isIntersecting);
      host.style.setProperty("--illos-play", visible ? "running" : "paused");
      loop();
    },
    { threshold: 0.25 },
  );
  io.observe(host);
  return () => {
    stopped = true;
    io.disconnect();
  };
}

// The Shader runtime, inline: it draws the illustration's field on a canvas beneath the SVG.
/**
 * The Shader style's runtime. A Shader SVG carries its field as GLSL in <metadata class="illos-field">;
 * mountShader draws that field on a canvas beneath the SVG in the bar look, taking its time from the SVG's clock
 * animation, so whatever plays, pauses, restarts or holds the SVG's CSS animations drives the field too. Erasable
 * TypeScript only: Node runs this file as is, and the site and the registry ship it as is.
 */

/** Helpers every field may use, and its one uniform, uT (seconds into the cycle). */
const PRELUDE = `precision highp float;
uniform float uT;
const float TAU = 6.2831853;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float blob(vec2 p, vec2 c, float r, float k) { vec2 d = p - c; return k * exp(-dot(d, d) / (r * r)); }
float sm(float a, float b, float t) { return smoothstep(a, b, t); }
float ease(float u) { u = clamp(u, 0.0, 1.0); return u < 0.5 ? 4.0 * u * u * u : 1.0 - pow(-2.0 * u + 2.0, 3.0) / 2.0; }
float seg(vec2 p, vec2 a, vec2 b, float r, float k) { vec2 ab = b - a; float h = clamp(dot(p - a, ab) / dot(ab, ab), 0.0, 1.0); vec2 d = p - a - ab * h; return k * exp(-dot(d, d) / (r * r)); }
float noise(vec2 p) { vec2 i = floor(p); vec2 f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y); }
`;

/**
 * One value per bar cell (columns every 7 px from x = 4, cells on a 9.4 period), with the cell's grain. The field pass
 * and the still's sampler both use it, so the still is the live field's first frame.
 */
const CELL = `
float cellValue(float col, float cell) {
  return field(vec2(4.0 + col * 7.0, cell * 9.4 + 3.5), uT) * (0.7 + 0.3 * hash(vec2(col, cell)));
}`;

/** The field pass: one pixel per cell into a COLS x CELLS texture, the value packed into red (coarse) and green (fine). */
const FIELD_MAIN = `
void main() {
  float v = clamp(cellValue(floor(gl_FragCoord.x), floor(gl_FragCoord.y)) / 4.0, 0.0, 0.99999);
  gl_FragColor = vec4(floor(v * 255.0) / 255.0, fract(v * 255.0), 0.0, 1.0);
}`;

/** The canvas in cells for a frame of w x h: columns every 7 from x = 4 to w - 2, cells of 9.4 down h (720 x 460: 103 x 49). */
const gridFor = (w: number, h: number): [number, number] => [Math.floor((w - 6) / 7) + 1, Math.ceil(h / 9.4)];

/**
 * The bar pass: per pixel, read its cell's value from the field pass and draw the bar look (ink and width by density,
 * screen lines, grid, sparks). Cheap per pixel; the field itself is worked out once per cell, not once per pixel.
 * As it is, it paints the ground too. With CLEAR defined (mountShader) the ground is left clear, so the page, a
 * gradient or a section edge, shows through: the look is painted over black and over white, and how much the two
 * differ is how much ground shows at that pixel.
 */
const BARS = `precision highp float;
uniform vec2 uRes; uniform vec2 uGrid; uniform float uDpr; uniform vec3 uGround; uniform vec3 uInk; uniform sampler2D uField;
uniform vec3 uR1; uniform vec3 uR2; uniform vec3 uR3; uniform vec3 uR4; uniform vec3 uSpark;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
vec3 paint(vec3 uGround, vec2 px, float v, float col, float cx, float cell) {
  vec3 color = uGround;
  if (mod(px.x, 96.0) < 1.0 || mod(px.y, 96.0) < 1.0) color = mix(uGround, uInk, 0.09);
  float dx = abs(px.x - cx);
  color = mix(color, mix(uGround, uR4, 0.18), 1.0 - smoothstep(0.3, 0.7, dx));
  float w = 0.0; vec3 c = uR4; float a = 1.0;
  if (v >= 0.72) { w = 3.6; c = uR1; } else if (v >= 0.48) { w = 2.7; c = uR2; } else if (v >= 0.27) { w = 1.8; c = uR3; } else if (v >= 0.1) { w = 1.1; c = uR4; a = 0.55; }
  float bar = step(mod(px.y, 9.4), 7.0) * (1.0 - smoothstep(w * 0.5 - 0.35, w * 0.5 + 0.35, dx)) * step(0.001, w);
  color = mix(color, c, bar * a);
  if (w > 3.5 && hash(vec2(col, cell * 1.3)) > 0.55 && abs(mod(px.y, 9.4) - 3.0) < 0.8 && dx < 0.7) color = uSpark;
  return color;
}
void main() {
  vec2 px = vec2(gl_FragCoord.x, uRes.y * uDpr - gl_FragCoord.y) / uDpr;
  float col = floor((px.x - 4.0) / 7.0 + 0.5);
  float cx = 4.0 + col * 7.0;
  float cell = floor(px.y / 9.4);
  vec4 e = texture2D(uField, (vec2(col, cell) + 0.5) / uGrid);
  float v = (e.r + e.g / 255.0) * 4.0;
  if (col < 0.0 || col > uGrid.x - 1.0) v = 0.0;
#ifdef CLEAR
  vec3 onBlack = paint(vec3(0.0), px, v, col, cx, cell);
  vec3 onWhite = paint(vec3(1.0), px, v, col, cx, cell);
  gl_FragColor = vec4(onBlack, clamp(1.0 - (onWhite.g - onBlack.g), 0.0, 1.0));
#else
  gl_FragColor = vec4(paint(uGround, px, v, col, cx, cell), 1.0);
#endif
}`;

/** The field pass for a field: the prelude, T (the cycle in seconds, which a field may use), the field, one value per cell. */
const fragmentFor = (field: string, cycle: number): string => `${PRELUDE}const float T = ${cycle.toFixed(3)};
${field}
${CELL}${FIELD_MAIN}`;

/** The cycle and the field a Shader SVG carries, or null for any other SVG. */
function readField(svg: Element): { cycle: number; field: string } | null {
  const meta = svg.querySelector("metadata.illos-field");
  if (!meta) return null;
  const cycle = Number(meta.getAttribute("data-cycle"));
  const field = (meta.textContent ?? "").trim();
  return cycle > 0 && /float\s+field\s*\(/.test(field) ? { cycle, field } : null;
}

const hex = (value: string, fallback: number[]): number[] => {
  const h = value.trim().replace("#", "");
  const full = h.length === 3 ? h.replace(/./g, "$&$&") : h;
  return /^[0-9a-f]{6}$/i.test(full) ? [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255) : fallback;
};

/** The light ramp and spark, used when the page sets no --illos-ramp-*. */
const RAMP = ["#16645a", "#1daf9c", "#3fdec9", "#c2f5ee"].map((c) => hex(c, [0, 0, 0]));
const SPARK = hex("#0b463e", [0, 0, 0]);

let probe: CanvasRenderingContext2D | null | undefined;
const unread = new Set<string>();
/**
 * Any CSS colour a role is set to (hex, rgb(), oklch(), a named colour) as sRGB from 0 to 1, read through a 1 px canvas.
 * A value the browser cannot read gives the fallback, with one console warning per value, never a silent white.
 */
function colour(value: string, fallback: number[]): number[] {
  const v = value.trim();
  if (!v) return fallback;
  const quick = hex(v, []);
  if (quick.length) return quick;
  probe ??= document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  if (probe) {
    probe.fillStyle = "#010203";
    probe.fillStyle = v; // a value the canvas cannot read leaves the marker
    if (probe.fillStyle !== "#010203") {
      probe.clearRect(0, 0, 1, 1);
      probe.fillRect(0, 0, 1, 1);
      const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
      return [r! / 255, g! / 255, b! / 255];
    }
  }
  if (!unread.has(v)) {
    unread.add(v);
    console.warn(`Illos: the colour "${v}" could not be read, so the default is used.`);
  }
  return fallback;
}

/**
 * Draws the field of the Shader SVG inside `host` on a canvas beneath it, with a clear ground so the page shows
 * through, and marks the host `illos-live` (CSS then hides the still). Draws a frame whenever the clock's time changed; while the clock is not running it checks every
 * 250 ms, and an `illos:draw` event on the host draws at once (the preview renderer sends it after a seek). Does
 * nothing, and leaves the still, under reduced motion, without a GPU for WebGL, or if the field does not compile. Returns cleanup.
 */
const SOFTWARE = /swiftshader|llvmpipe|softpipe|software|basic render/i;

/**
 * A WebGL context only when a GPU draws it, else null. Drawn in software, a field holds the page's main thread every
 * frame, so the still stays instead. The flag catches a blocklisted GPU; the renderer's name catches a software
 * renderer that some browsers still hand out as WebGL.
 */
function hardwareGl(canvas: HTMLCanvasElement, attributes: WebGLContextAttributes = {}): WebGLRenderingContext | null {
  // Asked once per page: on a software renderer each ask waits on the busy GPU process, and fields mount again and again.
  if (software) return null;
  const gl = canvas.getContext("webgl", { ...attributes, failIfMajorPerformanceCaveat: true }) as WebGLRenderingContext | null;
  if (software === undefined) {
    const info = gl?.getExtension("WEBGL_debug_renderer_info");
    software = !gl || Boolean(info && SOFTWARE.test(String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL))));
  }
  if (!software) return gl;
  gl?.getExtension("WEBGL_lose_context")?.loseContext();
  return null;
}
let software: boolean | undefined;

function mountShader(host: HTMLElement): () => void {
  const svg = host.querySelector("svg");
  const spec = svg ? readField(svg) : null;
  if (!svg || !spec) return () => {};
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return () => {};
  const canvas = document.createElement("canvas");
  const gl = hardwareGl(canvas, { antialias: false });
  if (!gl) return () => {};
  const shader = (type: number, src: string) => {
    const s = gl.createShader(type);
    if (!s) return null;
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
  };
  const vs = shader(gl.VERTEX_SHADER, "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}");
  const link = (fs: WebGLShader | null) => {
    const prog = gl.createProgram();
    if (!vs || !fs || !prog) return null;
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.bindAttribLocation(prog, 0, "p");
    gl.linkProgram(prog);
    return gl.getProgramParameter(prog, gl.LINK_STATUS) ? prog : null;
  };
  // The frame comes from the file's viewBox (a 640 x 720 portrait scene as well as the usual 720 x 460).
  const [vw, vh] = (svg.getAttribute("viewBox") ?? "").trim().split(/[\s,]+/).slice(2).map(Number);
  const [fw, fh] = vw! > 0 && vh! > 0 ? [vw!, vh!] : [720, 460];
  const [COLS, CELLS] = gridFor(fw, fh);
  const fieldProg = link(shader(gl.FRAGMENT_SHADER, fragmentFor(spec.field, spec.cycle)));
  const barsProg = link(shader(gl.FRAGMENT_SHADER, `#define CLEAR
${BARS}`));
  if (!fieldProg || !barsProg) return () => {};
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  // The field pass draws into a small texture, one texel per cell; the bar pass reads it.
  const cells = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, cells);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, COLS, CELLS, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  const fbo = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, cells, 0);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  const uT = gl.getUniformLocation(fieldProg, "uT");
  const uRes = gl.getUniformLocation(barsProg, "uRes");
  const uGrid = gl.getUniformLocation(barsProg, "uGrid");
  const uDpr = gl.getUniformLocation(barsProg, "uDpr");
  const uInk = gl.getUniformLocation(barsProg, "uInk");
  const uRamp = [1, 2, 3, 4].map((i) => gl.getUniformLocation(barsProg, `uR${i}`));
  const uSpark = gl.getUniformLocation(barsProg, "uSpark");
  const uField = gl.getUniformLocation(barsProg, "uField");

  canvas.className = "illos-field-canvas";
  svg.parentNode?.insertBefore(canvas, svg);
  host.classList.add("illos-live");
  const clock = svg.querySelector(".sh-clock");
  let last = -1;
  let size = "";
  let raf = 0;
  let timer = 0;
  let gone = false;

  // Layout and colours are read at most every half second: a read every frame would force style and layout each frame
  // while the labels animate. A resize or a theme switch shows within half a second.
  let measured = -Infinity;
  let w = 1;
  let h = 1;
  let ink = [0.165, 0.165, 0.165];
  let ramp = RAMP;
  let spark = SPARK;
  const measure = () => {
    const box = svg.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    w = Math.max(1, Math.round(box.width * dpr));
    h = Math.max(1, Math.round(box.height * dpr));
    const style = getComputedStyle(svg);
    // The page's role first, then the SVG's own fallback, which follows the system theme like the still does.
    const role = (r: string) => style.getPropertyValue(`--illos-${r}`).trim() || style.getPropertyValue(`--_${r}`);
    ink = colour(role("ink"), [0.165, 0.165, 0.165]);
    ramp = RAMP.map((fallback, i) => colour(role(`ramp-${i + 1}`), fallback));
    spark = colour(role("spark"), SPARK);
    measured = Date.now();
  };
  const draw = () => {
    const anim = clock?.getAnimations?.()[0];
    const t = (((Number(anim?.currentTime) || 0) / 1000) % spec.cycle + spec.cycle) % spec.cycle;
    if (Date.now() - measured > 500) measure();
    const look = `${w}x${h} ${ink} ${ramp} ${spark}`; // a resize or a theme switch redraws even while the clock is held
    if (t !== last || look !== size) {
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
      size = look;
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.viewport(0, 0, COLS, CELLS);
      gl.useProgram(fieldProg);
      gl.uniform1f(uT, t);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, w, h);
      gl.useProgram(barsProg);
      gl.uniform2f(uRes, fw, fh);
      gl.uniform2f(uGrid, COLS, CELLS);
      gl.uniform1f(uDpr, w / fw);
      gl.uniform3fv(uInk, ink);
      ramp.forEach((c, i) => gl.uniform3fv(uRamp[i]!, c));
      gl.uniform3fv(uSpark, spark);
      gl.bindTexture(gl.TEXTURE_2D, cells);
      gl.uniform1i(uField, 0);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      last = t;
    }
    return anim?.playState === "running";
  };
  // After the clock stops (a cycle ended, or it paused), keep watching every frame for a while: a player restarts a
  // loop a frame later, and the field must follow at once. Then sleep, checking every 250 ms.
  let idle = 0;
  const tick = () => {
    raf = 0;
    timer = 0;
    if (gone) return;
    idle = draw() ? 0 : idle + 1;
    if (idle < 20) raf = requestAnimationFrame(tick);
    else timer = window.setTimeout(tick, 250);
  };
  const now = () => {
    if (!gone) draw();
  };
  const stop = () => {
    if (gone) return; // a late context loss after cleanup must not touch a host another mount now owns
    gone = true;
    cancelAnimationFrame(raf);
    clearTimeout(timer);
    host.removeEventListener("illos:draw", now);
    canvas.remove();
    host.classList.remove("illos-live");
    gl.getExtension("WEBGL_lose_context")?.loseContext(); // free the context now: browsers cap how many are live
  };
  canvas.addEventListener("webglcontextlost", stop);
  host.addEventListener("illos:draw", now);
  tick();
  return stop;
}
