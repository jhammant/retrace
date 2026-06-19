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

const Vignette: React.FC = () => (
  <AbsoluteFill
    style={{
      background:
        "radial-gradient(120% 90% at 70% -10%, rgba(255,122,69,0.10), transparent 55%), radial-gradient(140% 120% at 50% 120%, rgba(0,0,0,0.6), transparent 60%)",
    }}
  />
);

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

// --- timeline ---
const FADE = 16;
const scenes: { d: number; el: React.ReactNode }[] = [
  { d: 105, el: <Brand /> },
  { d: 170, el: <Shot src="now.png" kicker="RIGHT NOW" title="See what you're doing" sub="captioned on-device" /> },
  { d: 170, el: <Shot src="timeline.png" kicker="REWIND" title="Scrub back through your day" /> },
  { d: 175, el: <Shot src="search.png" kicker="FIND" title="Search your memory" sub="text · semantic · hybrid" /> },
  { d: 200, el: <Shot src="stats.png" kicker="INSIGHT" title="Your time + system load" /> },
  { d: 150, el: <Features /> },
  { d: 150, el: <Outro /> },
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
