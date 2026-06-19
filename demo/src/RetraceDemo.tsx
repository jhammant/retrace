import React from "react";
import {
  AbsoluteFill,
  Img,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";

// --- design tokens (match Retrace) ---
const BG = "#0b0a09";
const EMBER = "#ff7a45";
const EMBER2 = "#ffb27a";
const CYAN = "#58cabb";
const INK = "#f3ece1";
const INK_DIM = "#a89f92";
const INK_FAINT = "#6d665b";
const LINE = "rgba(255,238,220,0.10)";
const SERIF = 'Georgia, "Times New Roman", serif';
const MONO = 'Menlo, "SF Mono", monospace';

const Vignette: React.FC = () => {
  const frame = useCurrentFrame();
  const gx = 62 + Math.sin(frame / 130) * 18;
  const gy = -8 + Math.cos(frame / 170) * 10;
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(120% 90% at ${gx}% ${gy}%, rgba(255,122,69,0.12), transparent 55%), radial-gradient(140% 120% at 50% 120%, rgba(0,0,0,0.55), transparent 60%)`,
      }}
    />
  );
};

// pure fade-up (spring is a pure fn, safe to call in loops)
const rise = (frame: number, fps: number, delay: number) => {
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return { opacity: interpolate(s, [0, 1], [0, 1]), y: interpolate(s, [0, 1], [28, 0]) };
};

// --- the Retrace mark ---
const Mark: React.FC<{ size: number }> = ({ size }) => (
  <svg viewBox="0 0 32 32" width={size} height={size}>
    <circle cx="16" cy="16" r="13" fill="none" stroke={EMBER} strokeWidth="2.4" />
    <path d="M16 8v8l5 3" fill="none" stroke={EMBER} strokeWidth="2.4" strokeLinecap="round" />
  </svg>
);

// --- Scene: brand intro ---
const Brand: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a = rise(frame, fps, 4);
  const b = rise(frame, fps, 16);
  const c = rise(frame, fps, 30);
  return (
    <AbsoluteFill style={{ background: BG, justifyContent: "center", alignItems: "center" }}>
      <Vignette />
      <div style={{ display: "flex", alignItems: "center", gap: 26, opacity: a.opacity, transform: `translateY(${a.y}px)` }}>
        <Mark size={92} />
        <div style={{ fontFamily: SERIF, fontSize: 110, color: INK, fontWeight: 500 }}>Retrace</div>
      </div>
      <div style={{ marginTop: 26, fontFamily: SERIF, fontSize: 40, color: INK_DIM, opacity: b.opacity, transform: `translateY(${b.y}px)` }}>
        a private, on-device rewind for your Mac
      </div>
      <div style={{ marginTop: 40, fontFamily: MONO, fontSize: 19, letterSpacing: 3, color: EMBER2, opacity: c.opacity, transform: `translateY(${c.y}px)` }}>
        100% ON-DEVICE · NO CLOUD · NO TELEMETRY
      </div>
    </AbsoluteFill>
  );
};

// --- Scene: a screenshot with a caption ---
const Shot: React.FC<{ src: string; kicker: string; title: string; sub?: string }> = ({
  src,
  kicker,
  title,
  sub,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();
  const scale = interpolate(frame, [0, durationInFrames], [1.03, 1.1]);
  const a = rise(frame, fps, 6);
  const b = rise(frame, fps, 14);
  return (
    <AbsoluteFill style={{ background: BG }}>
      <Vignette />
      <div style={{ position: "absolute", top: 70, left: 130, right: 130 }}>
        <div style={{ fontFamily: MONO, fontSize: 18, letterSpacing: 3, color: EMBER, opacity: a.opacity, transform: `translateY(${a.y}px)` }}>
          {kicker}
        </div>
        <div style={{ fontFamily: SERIF, fontSize: 52, color: INK, marginTop: 8, opacity: b.opacity, transform: `translateY(${b.y}px)` }}>
          {title}
          {sub ? <span style={{ color: INK_FAINT, fontSize: 34 }}> — {sub}</span> : null}
        </div>
      </div>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", top: 90 }}>
        <div
          style={{
            width: 1480,
            transform: `scale(${scale})`,
            borderRadius: 16,
            overflow: "hidden",
            border: `1px solid ${LINE}`,
            boxShadow: "0 50px 130px -30px rgba(0,0,0,0.85)",
          }}
        >
          <Img src={staticFile(src)} style={{ width: "100%", display: "block" }} />
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// --- Scene: a centered list of features ---
const Features: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const head = rise(frame, fps, 4);
  const items = [
    "🎵 Spotify & Apple Music",
    "⎇ Git commits",
    "📣 Notifications",
    "📅 Calendar",
    "✉️ Mail",
    "⌨️ Claude Code & shell history",
  ];
  return (
    <AbsoluteFill style={{ background: BG, justifyContent: "center", alignItems: "center" }}>
      <Vignette />
      <div style={{ fontFamily: MONO, fontSize: 18, letterSpacing: 3, color: EMBER, opacity: head.opacity, transform: `translateY(${head.y}px)` }}>
        ONE TIMELINE FOR EVERYTHING
      </div>
      <div style={{ fontFamily: SERIF, fontSize: 56, color: INK, marginTop: 14, opacity: head.opacity }}>
        Pluggable — record any app
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 18, marginTop: 50, width: 1200 }}>
        {items.map((it, i) => {
          const r = rise(frame, fps, 16 + i * 6);
          return (
            <div
              key={it}
              style={{
                fontFamily: MONO,
                fontSize: 26,
                color: INK_DIM,
                padding: "16px 26px",
                borderRadius: 999,
                border: `1px solid ${LINE}`,
                background: "rgba(255,122,69,0.06)",
                opacity: r.opacity,
                transform: `translateY(${r.y}px)`,
              }}
            >
              {it}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// --- Scene: privacy promise ---
const Privacy: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const head = rise(frame, fps, 4);
  const sub = rise(frame, fps, 12);
  const points = [
    "Raw screenshots are deleted every cycle — never stored",
    "Password managers & private/incognito windows are skipped",
    "Sensitive content is filtered on-device, before anything is saved",
    "Pauses when you step away · one-click Hidden mode",
  ];
  return (
    <AbsoluteFill style={{ background: BG, justifyContent: "center", alignItems: "center" }}>
      <Vignette />
      <div style={{ fontFamily: MONO, fontSize: 18, letterSpacing: 3, color: EMBER, opacity: head.opacity, transform: `translateY(${head.y}px)` }}>
        PRIVATE BY DESIGN
      </div>
      <div style={{ fontFamily: SERIF, fontSize: 60, color: INK, marginTop: 14, opacity: sub.opacity, transform: `translateY(${sub.y}px)` }}>
        Your data never leaves your Mac
      </div>
      <div style={{ marginTop: 50, display: "flex", flexDirection: "column", gap: 22, width: 1080 }}>
        {points.map((p, i) => {
          const r = rise(frame, fps, 22 + i * 8);
          return (
            <div key={p} style={{ display: "flex", alignItems: "center", gap: 18, opacity: r.opacity, transform: `translateY(${r.y}px)` }}>
              <svg width={30} height={30} viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="11" fill="none" stroke={EMBER} strokeWidth="1.6" />
                <path d="M7 12.5l3.2 3.2L17 8.5" fill="none" stroke={EMBER} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <div style={{ fontFamily: SERIF, fontSize: 30, color: INK_DIM }}>{p}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// --- Scene: Claude Code / MCP ---
const ClaudeMCP: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a = rise(frame, fps, 4);
  const b = rise(frame, fps, 12);
  const lines: { t: string; kind?: "prompt" | "dot" | "dim"; d: number }[] = [
    { t: "❯ what was I working on this afternoon?", kind: "prompt", d: 22 },
    { t: "", d: 30 },
    { t: "⏺ Let me check Retrace.", kind: "dot", d: 34 },
    { t: "  Called retrace — retrace_stats · retrace_timeline", kind: "dim", d: 42 },
    { t: "", d: 48 },
    { t: "⏺ You spent ~4h in VS Code on widget-api (auth.py),", kind: "dot", d: 56 },
    { t: "  reviewed the Q3 roadmap in Notion, and pinged", d: 62 },
    { t: "  #engineering on Slack. Chrome was only ~6 min.", d: 68 },
    { t: "  🎵 M83 played in the background.", kind: "dim", d: 74 },
  ];
  return (
    <AbsoluteFill style={{ background: BG }}>
      <Vignette />
      <div style={{ position: "absolute", top: 70, left: 130, right: 130 }}>
        <div style={{ fontFamily: MONO, fontSize: 18, letterSpacing: 3, color: EMBER, opacity: a.opacity, transform: `translateY(${a.y}px)` }}>
          ASK YOUR AI
        </div>
        <div style={{ fontFamily: SERIF, fontSize: 52, color: INK, marginTop: 8, opacity: b.opacity, transform: `translateY(${b.y}px)` }}>
          Works as an MCP server
          <span style={{ color: INK_FAINT, fontSize: 34 }}> — query your day from Claude Code</span>
        </div>
      </div>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", top: 110 }}>
        <div style={{ width: 1240, background: "#141210", border: `1px solid ${LINE}`, borderRadius: 14, overflow: "hidden", boxShadow: "0 50px 120px -30px rgba(0,0,0,0.85)" }}>
          <div style={{ height: 46, background: "#1c1916", borderBottom: `1px solid ${LINE}`, display: "flex", alignItems: "center", paddingLeft: 18, gap: 9 }}>
            {["#ff6f6f", "#ffd166", "#74e0a3"].map((c) => (
              <div key={c} style={{ width: 13, height: 13, borderRadius: "50%", background: c }} />
            ))}
            <div style={{ fontFamily: MONO, fontSize: 17, color: INK_FAINT, marginLeft: 14 }}>claude — widget-api</div>
          </div>
          <div style={{ padding: "34px 44px", fontFamily: MONO, fontSize: 25, lineHeight: 1.75, minHeight: 360 }}>
            {lines.map((ln, i) => {
              const r = rise(frame, fps, ln.d);
              const color = ln.kind === "prompt" ? EMBER2 : ln.kind === "dim" ? INK_FAINT : INK;
              const marker = ln.t[0];
              const rest = ln.t.slice(1);
              return (
                <div key={i} style={{ opacity: r.opacity, transform: `translateY(${r.y / 3}px)`, color, minHeight: 36 }}>
                  {marker === "⏺" || marker === "❯" ? (
                    <span>
                      <span style={{ color: EMBER }}>{marker}</span>
                      {rest}
                    </span>
                  ) : (
                    ln.t
                  )}
                </div>
              );
            })}
          </div>
        </div>
        <div style={{ marginTop: 26, fontFamily: MONO, fontSize: 21, color: INK_DIM, opacity: rise(frame, fps, 86).opacity }}>
          $ claude mcp add retrace &nbsp;·&nbsp; 7 read-only tools &nbsp;·&nbsp; no writes, no cloud
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// --- Scene: outro ---
const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a = rise(frame, fps, 4);
  const b = rise(frame, fps, 16);
  const c = rise(frame, fps, 28);
  return (
    <AbsoluteFill style={{ background: BG, justifyContent: "center", alignItems: "center" }}>
      <Vignette />
      <div style={{ display: "flex", alignItems: "center", gap: 20, opacity: a.opacity, transform: `translateY(${a.y}px)` }}>
        <Mark size={64} />
        <div style={{ fontFamily: SERIF, fontSize: 76, color: INK }}>Retrace</div>
      </div>
      <div style={{ marginTop: 28, fontFamily: SERIF, fontSize: 40, color: INK_DIM, opacity: b.opacity, transform: `translateY(${b.y}px)` }}>
        open-source · Apache-2.0 · macOS
      </div>
      <div style={{ marginTop: 40, fontFamily: MONO, fontSize: 30, color: EMBER2, opacity: c.opacity, transform: `translateY(${c.y}px)` }}>
        github.com/jhammant/retrace
      </div>
    </AbsoluteFill>
  );
};

// --- Scene: total web-page recall ---
const mark = (text: string, term: string) => {
  const parts = text.split(new RegExp(`(${term})`, "i"));
  return parts.map((p, i) =>
    p.toLowerCase() === term.toLowerCase() ? (
      <span key={i} style={{ background: "rgba(255,122,69,0.18)", color: EMBER2, borderRadius: 3, padding: "0 3px" }}>{p}</span>
    ) : (
      <span key={i}>{p}</span>
    )
  );
};

const PageMemory: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a = rise(frame, fps, 4);
  const b = rise(frame, fps, 12);
  const box = rise(frame, fps, 20);
  const results = [
    { app: "Arc", hue: "#6aa9ff", title: "Acme — Pricing", time: "Tue · 2:41pm",
      snip: "…the Team plan adds SSO, audit logs and priority support at $40/seat — Pro stays at $20…", d: 34 },
    { app: "Arc", hue: "#b07aff", title: "Stripe Docs — Rate limits", time: "Mon · 5:07pm",
      snip: "…the API allows 100 read requests/sec in live mode; bursts are smoothed over a 1-second window…", d: 46 },
  ];
  return (
    <AbsoluteFill style={{ background: BG }}>
      <Vignette />
      <div style={{ position: "absolute", top: 70, left: 130, right: 130 }}>
        <div style={{ fontFamily: MONO, fontSize: 18, letterSpacing: 3, color: EMBER, opacity: a.opacity, transform: `translateY(${a.y}px)` }}>
          TOTAL RECALL
        </div>
        <div style={{ fontFamily: SERIF, fontSize: 52, color: INK, marginTop: 8, opacity: b.opacity, transform: `translateY(${b.y}px)` }}>
          Every web page you read — remembered
          <span style={{ color: INK_FAINT, fontSize: 32 }}> — full page text, not just the screen</span>
        </div>
      </div>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", top: 100 }}>
        <div style={{ width: 1260 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14, background: "#141210", border: `1px solid ${EMBER}`, boxShadow: "0 0 0 5px rgba(255,122,69,0.12)", borderRadius: 14, padding: "20px 26px", opacity: box.opacity, transform: `translateY(${box.y}px)` }}>
            <span style={{ color: INK_FAINT, fontSize: 28 }}>⌕</span>
            <span style={{ fontFamily: SERIF, fontSize: 30, color: INK }}>that pricing table I read on Tuesday</span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 16, letterSpacing: 1, color: EMBER, marginTop: 22 }}>2 RESULTS · SEMANTIC</div>
          {results.map((r) => {
            const rr = rise(frame, fps, r.d);
            return (
              <div key={r.title} style={{ marginTop: 16, background: "linear-gradient(180deg,#141210,rgba(20,18,16,0.6))", border: `1px solid ${LINE}`, borderRadius: 14, padding: "18px 22px", opacity: rr.opacity, transform: `translateY(${rr.y}px)` }}>
                <div style={{ display: "flex", alignItems: "center", gap: 13 }}>
                  <span style={{ width: 30, height: 30, borderRadius: 8, background: r.hue, display: "inline-flex", alignItems: "center", justifyContent: "center", color: "#0b0a09", fontFamily: MONO, fontWeight: 700, fontSize: 15 }}>{r.app[0]}</span>
                  <span style={{ fontFamily: MONO, fontSize: 20, color: INK }}>{r.app}</span>
                  <span style={{ marginLeft: "auto", fontFamily: MONO, fontSize: 18, color: INK_FAINT }}>{r.title} · {r.time}</span>
                </div>
                <div style={{ fontFamily: MONO, fontSize: 21, color: INK_DIM, marginTop: 12, lineHeight: 1.5 }}>
                  {mark(r.snip, "pricing")}
                </div>
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// --- Scene: ask Claude to automate where the time went ---
const AutomateDemo: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a = rise(frame, fps, 4);
  const b = rise(frame, fps, 12);
  const lines: { t: string; kind?: "prompt" | "dot" | "dim"; d: number }[] = [
    { t: "❯ where's my time actually going? automate the most repetitive bit.", kind: "prompt", d: 22 },
    { t: "", d: 30 },
    { t: "⏺ Reading your Retrace timeline…", kind: "dot", d: 34 },
    { t: "  Called retrace — retrace_stats · retrace_list_apps", kind: "dim", d: 42 },
    { t: "", d: 48 },
    { t: "⏺ Biggest sink: ~6h this week copying Linear issues into", kind: "dot", d: 56 },
    { t: "  Slack stand-ups — 28 times, almost identical each day.", d: 62 },
    { t: "  That's mechanical. Automating it now.", d: 68 },
    { t: "  ✎ wrote scripts/linear_to_slack.py · scheduled daily 9am", kind: "dim", d: 80 },
    { t: "", d: 86 },
    { t: "⏺ Done — that's ~5h/week back. Want your stand-up notes next?", kind: "dot", d: 92 },
  ];
  return (
    <AbsoluteFill style={{ background: BG }}>
      <Vignette />
      <div style={{ position: "absolute", top: 64, left: 130, right: 130 }}>
        <div style={{ fontFamily: MONO, fontSize: 18, letterSpacing: 3, color: EMBER, opacity: a.opacity, transform: `translateY(${a.y}px)` }}>
          FROM INSIGHT TO ACTION
        </div>
        <div style={{ fontFamily: SERIF, fontSize: 50, color: INK, marginTop: 8, opacity: b.opacity, transform: `translateY(${b.y}px)` }}>
          Then ask your agent to automate it
        </div>
      </div>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", top: 96 }}>
        <div style={{ width: 1280, background: "#141210", border: `1px solid ${LINE}`, borderRadius: 14, overflow: "hidden", boxShadow: "0 50px 120px -30px rgba(0,0,0,0.85)" }}>
          <div style={{ height: 46, background: "#1c1916", borderBottom: `1px solid ${LINE}`, display: "flex", alignItems: "center", paddingLeft: 18, gap: 9 }}>
            {["#ff6f6f", "#ffd166", "#74e0a3"].map((c) => (
              <div key={c} style={{ width: 13, height: 13, borderRadius: "50%", background: c }} />
            ))}
            <div style={{ fontFamily: MONO, fontSize: 17, color: INK_FAINT, marginLeft: 14 }}>claude</div>
          </div>
          <div style={{ padding: "30px 42px", fontFamily: MONO, fontSize: 23, lineHeight: 1.7, minHeight: 380 }}>
            {lines.map((ln, i) => {
              const r = rise(frame, fps, ln.d);
              const color = ln.kind === "prompt" ? EMBER2 : ln.kind === "dim" ? INK_FAINT : INK;
              const marker = ln.t[0];
              return (
                <div key={i} style={{ opacity: r.opacity, transform: `translateY(${r.y / 3}px)`, color, minHeight: 34 }}>
                  {marker === "⏺" || marker === "❯" ? (
                    <span>
                      <span style={{ color: EMBER }}>{marker}</span>
                      {ln.t.slice(1)}
                    </span>
                  ) : (
                    ln.t
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// --- timeline ---
const FADE = 16;
const scenes: { d: number; el: React.ReactNode }[] = [
  { d: 100, el: <Brand /> },
  { d: 145, el: <Shot src="now.png" kicker="RIGHT NOW" title="See what you're doing" sub="captioned on-device" /> },
  { d: 140, el: <Shot src="timeline.png" kicker="REWIND" title="Scrub back through your day" /> },
  { d: 195, el: <PageMemory /> },
  { d: 165, el: <Shot src="stats.png" kicker="INSIGHT" title="Your time + system load" /> },
  { d: 205, el: <ClaudeMCP /> },
  { d: 235, el: <AutomateDemo /> },
  { d: 140, el: <Features /> },
  { d: 145, el: <Privacy /> },
  { d: 145, el: <Outro /> },
];

export const DURATION =
  scenes.reduce((a, s) => a + s.d, 0) - FADE * (scenes.length - 1);

export const RetraceDemo: React.FC = () => {
  return (
    <AbsoluteFill style={{ background: BG }}>
      <TransitionSeries>
        {scenes.map((s, i) => (
          <React.Fragment key={i}>
            <TransitionSeries.Sequence durationInFrames={s.d}>{s.el}</TransitionSeries.Sequence>
            {i < scenes.length - 1 ? (
              <TransitionSeries.Transition
                presentation={fade()}
                timing={linearTiming({ durationInFrames: FADE })}
              />
            ) : null}
          </React.Fragment>
        ))}
      </TransitionSeries>
    </AbsoluteFill>
  );
};
